"""Fix agent: executes correction commands from an RCA report via MCP."""

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

from my_agent_app.agents.llm import get_agent_llm

logger = logging.getLogger(__name__)

# Tools the fix agent may use (write + read)
FIX_TOOLS = {
    "kubectl_get",
    "kubectl_describe",
    "kubectl_logs",
    "kubectl_apply",
    "kubectl_patch",
    "kubectl_delete",
    "kubectl_scale",
    "kubectl_rollout",
    "kubectl_create",
}

FIX_SYSTEM_PROMPT = """\
You are a platform engineer specialized in automated remediation of Kubernetes problems.

You have received a diagnostics report with the identified root cause and suggested fix commands.
Your task is to EXECUTE the suggested fixes using the available tools.

## RULES
- Execute ONLY the commands listed in the "Recommended Fix" and "Suggested Command"
  sections of the report.
- Do not invent fixes not documented in the report.
- After executing each command, verify the result with kubectl_get or kubectl_describe.
- If a command fails, try the alternative suggested in the report.
- Do not execute kubectl_delete on critical resources (nodes, system namespaces, PVs)
  without explicit confirmation.
- At the end, report what was executed, the result, and whether the problem was resolved.

## RESPONSE FORMAT

## Fix Result

**Status:** SUCCESS | PARTIAL FAILURE | FAILURE

### Actions Executed
- command 1: result
- command 2: result

### Current State
Description of the resource state after the fix.

### Observations
Any additional relevant information.\
"""


def _get_mcp_url() -> str:
    return os.environ.get("MCP_SERVER_URL", "http://mcp-server-kubernetes:3001/mcp")


async def _run_once(report_markdown: str) -> tuple[str, bool]:
    mcp_url = _get_mcp_url()

    mcp_client = MultiServerMCPClient(
        connections={
            "kubernetes": {
                "url": mcp_url,
                "transport": "streamable_http",
                "headers": {"Host": "localhost"},
            }
        }
    )

    all_tools = await mcp_client.get_tools()
    fix_tools = [t for t in all_tools if t.name in FIX_TOOLS]
    if not fix_tools:
        raise RuntimeError("MCP server returned no usable tools for fix execution")

    logger.info("Fix agent tools: %s", [t.name for t in fix_tools])

    llm = get_agent_llm()
    agent = create_agent(
        model=llm,
        tools=fix_tools,
        system_prompt=SystemMessage(content=FIX_SYSTEM_PROMPT),
    )

    input_messages = [
        HumanMessage(
            content=f"Execute the fixes for the following diagnostics report:\n\n{report_markdown}"
        )
    ]

    result = await agent.ainvoke({"messages": input_messages})
    messages = result.get("messages", [])
    last_ai = next(
        (m for m in reversed(messages) if hasattr(m, "content") and m.type == "ai"),
        None,
    )
    output: str = last_ai.content if last_ai else ""
    success = bool(output) and "SUCCESS" in output.upper()
    return output, success


_TRANSIENT_ERROR_TYPES = (
    "ConnectError",
    "RemoteProtocolError",
    "ReadError",
    "WriteError",
    "PoolTimeout",
    "ConnectTimeout",
    "ExceptionGroup",
)


def _is_transient(exc: Exception) -> bool:
    name = type(exc).__name__
    msg = str(exc)
    return any(t in name or t in msg for t in _TRANSIENT_ERROR_TYPES)


async def stream_fix_execution(report_markdown: str) -> AsyncGenerator[dict, None]:
    """
    Async generator that streams fix execution events in real time.
    Yields dicts: {type, content?, name?, args?, output?, success?}
    Types: info | tool_start | tool_end | ai_token | done | error
    """
    mcp_url = _get_mcp_url()
    mcp_token = os.environ.get("MCP_AUTH_TOKEN")
    headers = {"Host": "localhost"}
    if mcp_token:
        headers["X-MCP-AUTH"] = mcp_token

    yield {"type": "info", "content": f"Connecting to MCP Server at {mcp_url} …"}

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
    except Exception as exc:
        yield {"type": "error", "content": f"Failed to connect to MCP Server: {exc}"}
        return

    fix_tools = [t for t in all_tools if t.name in FIX_TOOLS]
    if not fix_tools:
        yield {"type": "error", "content": "No kubectl tools returned by MCP Server."}
        return

    tool_names = ", ".join(t.name for t in fix_tools)
    yield {
        "type": "info",
        "content": f"Connected. {len(fix_tools)} tools available: {tool_names}",
    }

    llm = get_agent_llm()
    agent = create_agent(
        model=llm,
        tools=fix_tools,
        system_prompt=SystemMessage(content=FIX_SYSTEM_PROMPT),
    )

    input_messages = [
        HumanMessage(
            content=f"Execute the fixes for the following diagnostics report:\n\n{report_markdown}"
        )
    ]

    yield {"type": "info", "content": "Sending report to Fix Agent (LangChain + Claude Sonnet) …"}

    ai_parts: list[str] = []

    try:
        async for event in agent.astream_events({"messages": input_messages}, version="v2"):
            kind = event.get("event", "")

            if kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                raw_input = event.get("data", {}).get("input", {})
                if isinstance(raw_input, dict):
                    args = "  ".join(
                        f"{k}={json.dumps(v)}"
                        for k, v in raw_input.items()
                        if v is not None and v != ""
                    )
                else:
                    args = str(raw_input)
                yield {"type": "tool_start", "name": tool_name, "args": args}

            elif kind == "on_tool_end":
                tool_name = event.get("name", "unknown")
                raw_output = event.get("data", {}).get("output", "")
                output_str = (
                    str(raw_output.content)
                    if hasattr(raw_output, "content")
                    else str(raw_output)
                )
                if len(output_str) > 3000:
                    output_str = output_str[:3000] + "\n… (output truncated)"
                yield {"type": "tool_end", "name": tool_name, "output": output_str}

            elif kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk:
                    content = getattr(chunk, "content", "")
                    if isinstance(content, str) and content:
                        ai_parts.append(content)
                        yield {"type": "ai_token", "content": content}
                    elif isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                text = item.get("text", "")
                                if text:
                                    ai_parts.append(text)
                                    yield {"type": "ai_token", "content": text}

    except Exception as exc:
        logger.exception("Fix agent streaming failed")
        yield {"type": "error", "content": str(exc)}
        return

    full_output = "".join(ai_parts)
    success = bool(full_output) and "SUCCESS" in full_output.upper()
    yield {"type": "done", "success": success, "output": full_output}


async def run_fix_execution(report_markdown: str, max_retries: int = 3) -> tuple[str, bool]:
    """
    Execute the fix suggested in the RCA report.
    Retries up to max_retries times on transient MCP connection errors.

    Returns (result_markdown: str, success: bool)
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return await _run_once(report_markdown)
        except Exception as exc:
            last_exc = exc
            if _is_transient(exc) and attempt < max_retries:
                wait = 10 * attempt
                logger.warning(
                    "Fix agent attempt %d/%d transient error; retrying in %ds: %s: %s",
                    attempt, max_retries, wait, type(exc).__name__, exc,
                )
                await asyncio.sleep(wait)
                continue
            logger.exception("Fix agent execution failed (attempt %d/%d)", attempt, max_retries)
            raise

    raise RuntimeError("Fix agent exhausted retries") from last_exc
