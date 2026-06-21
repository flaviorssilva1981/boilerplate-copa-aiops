"""RCA agent: LangChain 1.x create_agent + Claude Sonnet via Requesty + MCP Kubernetes (read-only)."""

import asyncio
import json
import logging
import os
import re

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

from my_agent_app.agents.llm import get_agent_llm

logger = logging.getLogger(__name__)

READ_ONLY_TOOLS = {"kubectl_get", "kubectl_describe", "kubectl_logs"}

SYSTEM_PROMPT = """\
You are a Kubernetes diagnostics specialist. Your role is to investigate and identify problems using the available tools.

## RULES
- Only diagnose and suggest fixes. NEVER execute kubectl_patch, kubectl_apply or kubectl_delete.
- Use ONLY the documented parameters of each tool.
- Investigate by GROUPED PROBLEM, not by individual event.
- Once you have root cause + sufficient evidence, move on to the next problem.
- NEVER call the same tool with the same parameters twice.
- Under **Events**, list the uid of EACH received event in exactly one problem.

## HOW TO INVESTIGATE
You have 3 read-only tools. Use them IMMEDIATELY to collect evidence:
- **kubectl_get**: overview of resources (status, restarts, age). Use `name` OR `labelSelector`, NEVER both together.
- **kubectl_describe**: full details (events, conditions, configuration).
- **kubectl_logs**: container logs. Use `previous: true` for logs from a previous run.

For each problem received:
1. Call kubectl_describe on the affected resource to get details
2. If needed, call kubectl_logs to see application errors
3. With the evidence collected, write the report

## ERROR HANDLING
- "name cannot be provided when a selector is specified" -> Use ONLY name OR labelSelector
- If a tool fails 2 times, record the error as evidence and continue

## SEVERITY
- **CRITICAL**: Pod/Deployment unavailable, service down
- **HIGH**: Frequent restarts, OOMKilled, exhausted resources
- **MEDIUM**: Recurring warnings but service functional
- **LOW**: Informational events, cosmetic issues

## RESPONSE FORMAT (Markdown)

# Kubernetes Diagnostics Report

## Summary
| Total | Critical | High | Medium | Low |
|-------|----------|------|--------|-----|
| X     | X        | X    | X      | X   |

---

## Problem 1: [concise root cause]
- **Severity:** CRITICAL | HIGH | MEDIUM | LOW
- **Namespace:** affected-namespace
- **Affected Resources:** pod1, deployment/name
- **Events:** event-uid-1, event-uid-2

### Root Cause
Clear description of the problem.

### Evidence
- evidence 1
- evidence 2

### Recommended Fix
Specific action to resolve the issue.

### Suggested Command
kubectl patch deployment name -n namespace --type=merge -p '{"spec":...}'

Repeat the section for each problem. Respond ONLY with the markdown report.\
"""


def _get_max_iterations() -> int:
    raw = os.environ.get("AGENT_MAX_ITERATIONS", "25")
    try:
        value = int(raw)
        if value > 0:
            return value
    except ValueError:
        pass
    logger.warning("Invalid AGENT_MAX_ITERATIONS=%r; using default 25", raw)
    return 25


def _get_mcp_url() -> str:
    return os.environ.get("MCP_SERVER_URL", "http://mcp-server-kubernetes:3001/mcp")


def _get_mcp_auth_token() -> str | None:
    return os.environ.get("MCP_AUTH_TOKEN")


def _split_into_problem_sections(markdown: str, event_uids: list[str]) -> list[dict]:
    """Split the agent response into one dict per ## Problema section."""
    # Split keeping the full heading (capturing group preserves the delimiter)
    parts = re.split(r"(?m)(^## Problem \d+)", markdown)
    # parts = [preamble, "## Problema 1", ": title\n...", "## Problema 2", ": title2\n...", ...]
    problems = []
    uids_used: set[str] = set()

    i = 1
    while i < len(parts) - 1:
        heading = parts[i]          # e.g. "## Problema 1"
        body = parts[i + 1]         # e.g. ": Vault selado...\n..."
        section_md = heading + body
        uid_matches = re.findall(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            section_md,
            re.IGNORECASE,
        )
        section_uids = [u for u in uid_matches if u in event_uids and u not in uids_used]
        uids_used.update(section_uids)
        problems.append({"markdown": section_md, "event_uids": section_uids})
        i += 2

    unclaimed = [u for u in event_uids if u not in uids_used]
    if unclaimed and problems:
        problems[-1]["event_uids"].extend(unclaimed)
        logger.warning("Assigned %s unclaimed event UIDs to last problem section", len(unclaimed))

    return problems


async def _run_rca_once(events: list[dict]) -> list[dict]:
    """
    Run the RCA agent for a batch of Warning events.

    Returns a list of dicts: [{markdown: str, event_uids: list[str]}]
    One entry per identified problem.
    """
    formatted = json.dumps(events, ensure_ascii=False, indent=2)
    event_uids = [e["uid"] for e in events if e.get("uid")]
    mcp_url = _get_mcp_url()
    mcp_token = _get_mcp_auth_token()

    headers = {}
    if mcp_token:
        headers["X-MCP-AUTH"] = mcp_token

    # Add "Host: localhost" so the MCP SDK's DNS-rebinding host header validation
    # accepts the request (localhost is always in the default allowlist).
    headers["Host"] = "localhost"

    try:
        mcp_client = MultiServerMCPClient(
            connections={
                "kubernetes": {
                    "url": mcp_url,
                    "transport": "streamable_http",
                    "headers": headers,
                }
            }
        )
        all_tools = await mcp_client.get_tools()
    except Exception:
        logger.exception("Failed to connect to MCP server at %s", mcp_url)
        raise

    read_tools = [t for t in all_tools if t.name in READ_ONLY_TOOLS]
    if not read_tools:
        logger.warning(
            "No read-only tools found from MCP server. Available: %s",
            [t.name for t in all_tools],
        )
        raise RuntimeError("MCP server returned no read-only tools")

    logger.info("MCP tools loaded: %s", [t.name for t in read_tools])

    llm = get_agent_llm()
    max_iterations = _get_max_iterations()

    agent = create_agent(
        model=llm,
        tools=read_tools,
        system_prompt=SystemMessage(content=SYSTEM_PROMPT),
        middleware=[ModelCallLimitMiddleware(run_limit=max_iterations, exit_behavior="end")],
    )

    input_messages = [HumanMessage(content=f"Events received for investigation:\n\n{formatted}")]

    try:
        result = await agent.ainvoke({"messages": input_messages})
        messages = result.get("messages", [])
        last_ai = next(
            (m for m in reversed(messages) if hasattr(m, "content") and m.type == "ai"),
            None,
        )
        output: str = last_ai.content if last_ai else ""
    except Exception:
        logger.exception("Agent execution failed")
        raise

    if not output.strip():
        logger.warning("Agent returned empty response")
        return [{"markdown": "", "event_uids": event_uids}]

    problems = _split_into_problem_sections(output, event_uids)
    if not problems:
        return [{"markdown": output, "event_uids": event_uids}]

    # Drop empty-markdown sections to avoid saving blank INCOMPLETO reports
    non_empty = [p for p in problems if p.get("markdown", "").strip()]
    return non_empty if non_empty else problems


_TRANSIENT_ERROR_TYPES = (
    "ConnectError",
    "RemoteProtocolError",
    "ReadError",
    "WriteError",
    "PoolTimeout",
    "ConnectTimeout",
    "ExceptionGroup",
)


def _is_transient_rca(exc: Exception) -> bool:
    name = type(exc).__name__
    msg = str(exc)
    return any(t in name or t in msg for t in _TRANSIENT_ERROR_TYPES)


async def run_rca_analysis(events: list[dict], max_retries: int = 3) -> list[dict]:
    """
    Run the RCA agent with automatic retry on transient MCP connection errors.
    Wraps _run_rca_once which contains the original logic.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return await _run_rca_once(events)
        except Exception as exc:
            last_exc = exc
            if _is_transient_rca(exc) and attempt < max_retries:
                wait = 10 * attempt
                logger.warning(
                    "RCA agent attempt %d/%d transient error; retrying in %ds: %s: %s",
                    attempt, max_retries, wait, type(exc).__name__, exc,
                )
                await asyncio.sleep(wait)
                continue
            logger.exception("RCA agent execution failed (attempt %d/%d)", attempt, max_retries)
            raise
    raise RuntimeError("RCA agent exhausted retries") from last_exc
