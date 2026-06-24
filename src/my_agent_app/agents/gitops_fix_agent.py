"""GitOps fix agent: applies RCA fixes via GitHub PR instead of live kubectl."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from my_agent_app.agents.llm import get_agent_llm
from my_agent_app.gitops.catalog import GITOPS_MANIFEST_CATALOG, PROTECTED_PATH_FRAGMENTS
from my_agent_app.gitops.github_client import GitHubClient, parse_gitops_repo

logger = logging.getLogger(__name__)

PLAN_SYSTEM_PROMPT = f"""\
You are a GitOps engineer. Given a Kubernetes RCA report, decide which manifest file
in the GitOps repository must be edited. Do NOT suggest kubectl patch — Argo CD self-heals
live changes.

{GITOPS_MANIFEST_CATALOG}

Respond with ONLY a JSON object (no markdown fences) using this schema:
{{
  "file_path": "kubernetes/argocd_cluster02/applications/core-tools/grafana.yaml",
  "change_summary": "Brief description of the YAML/Helm values change",
  "commit_message": "fix(grafana): set readiness probe initialDelaySeconds to 15",
  "pr_title": "AIOps: fix Grafana readiness probe startup failures",
  "pr_body": "Markdown body explaining the RCA and fix for reviewers",
  "skip": false,
  "skip_reason": ""
}}

If the fix cannot be expressed as a GitOps manifest change, set skip=true and explain in skip_reason.
"""

PATCH_SYSTEM_PROMPT = """\
You are a GitOps engineer editing a Kubernetes manifest file.
Apply ONLY the change described in the change summary. Preserve all unrelated content exactly.
Return ONLY the complete updated file content — no markdown fences, no explanation.
"""


def _parse_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM response did not contain JSON")
    return json.loads(text[start : end + 1])


def _is_protected_path(path: str) -> bool:
    lowered = path.lower()
    return any(fragment in lowered for fragment in PROTECTED_PATH_FRAGMENTS)


def _gitops_settings() -> dict[str, str | bool]:
    owner, repo = parse_gitops_repo()
    return {
        "owner": owner,
        "repo": repo,
        "work_branch": os.environ.get("GITOPS_WORK_BRANCH", "dev"),
        "deploy_branch": os.environ.get("GITOPS_DEPLOY_BRANCH", "main"),
        "auto_merge": os.environ.get("GITOPS_AUTO_MERGE", "false").lower() in ("1", "true", "yes"),
    }


async def stream_gitops_fix(report_markdown: str, report_id: str) -> AsyncGenerator[dict, None]:
    """
    Stream GitOps fix workflow events.
    Types: info | step | ai_token | done | error
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        yield {"type": "error", "content": "GITHUB_TOKEN is not configured."}
        return

    settings = _gitops_settings()
    owner = str(settings["owner"])
    repo = str(settings["repo"])
    work_branch = str(settings["work_branch"])
    deploy_branch = str(settings["deploy_branch"])
    auto_merge = bool(settings["auto_merge"])

    fix_branch = f"aiops/fix-{report_id.replace('-', '')[:12]}"
    repo_label = f"{owner}/{repo}"

    yield {
        "type": "info",
        "content": f"GitOps target: {repo_label} (branch {fix_branch} → PR → {deploy_branch})",
    }

    llm = get_agent_llm()

    yield {"type": "step", "content": "Analyzing report and selecting manifest file…"}
    plan_response = await llm.ainvoke(
        [
            SystemMessage(content=PLAN_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "RCA report:\n\n"
                    f"{report_markdown}\n\n"
                    "Return the JSON plan object."
                )
            ),
        ]
    )
    plan_text = str(plan_response.content)
    try:
        plan = _parse_json_response(plan_text)
    except (json.JSONDecodeError, ValueError) as exc:
        yield {"type": "error", "content": f"Failed to parse fix plan from LLM: {exc}"}
        return

    if plan.get("skip"):
        reason = plan.get("skip_reason") or "Fix not suitable for GitOps."
        yield {"type": "error", "content": f"GitOps fix skipped: {reason}"}
        return

    file_path = plan.get("file_path", "").strip()
    if not file_path:
        yield {"type": "error", "content": "LLM plan did not include file_path."}
        return

    change_summary = plan.get("change_summary", "")
    commit_message = plan.get("commit_message") or f"fix(aiops): update {file_path}"
    pr_title = plan.get("pr_title") or f"AIOps fix: {file_path}"
    pr_body = plan.get("pr_body") or (
        f"Automated GitOps fix from AIOps report `{report_id}`.\n\n{change_summary}"
    )

    yield {"type": "step", "content": f"Selected manifest: {file_path}"}

    gh = GitHubClient(token=token, owner=owner, repo=repo)

    try:
        yield {"type": "step", "content": f"Fetching {file_path} from {work_branch}…"}
        original = await gh.get_file(file_path, ref=work_branch)
    except Exception as exc:
        yield {"type": "error", "content": f"Failed to read {file_path} from GitHub: {exc}"}
        return

    yield {"type": "step", "content": "Generating patched manifest content…"}
    patch_response = await llm.ainvoke(
        [
            SystemMessage(content=PATCH_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"File: {file_path}\n"
                    f"Change summary: {change_summary}\n\n"
                    f"Original content:\n\n{original.content}\n\n"
                    "Return the full updated file."
                )
            ),
        ]
    )
    new_content = str(patch_response.content).strip()
    if new_content.startswith("```"):
        new_content = re.sub(r"^```\w*\n?", "", new_content)
        new_content = re.sub(r"\n?```$", "", new_content)

    if not new_content or new_content == original.content:
        yield {"type": "error", "content": "LLM produced no effective change to the manifest."}
        return

    try:
        yield {"type": "step", "content": f"Creating branch {fix_branch} from {work_branch}…"}
        base_sha = await gh.get_branch_sha(work_branch)
        await gh.create_branch(fix_branch, base_sha)

        yield {"type": "step", "content": f"Committing change to {fix_branch}…"}
        await gh.update_file(
            path=file_path,
            content=new_content,
            message=commit_message,
            branch=fix_branch,
            sha=original.sha,
        )

        yield {"type": "step", "content": f"Opening PR {fix_branch} → {deploy_branch}…"}
        pr = await gh.create_pull_request(
            title=pr_title,
            body=pr_body,
            head=fix_branch,
            base=deploy_branch,
        )
        yield {"type": "step", "content": f"Pull request #{pr.number}: {pr.html_url}"}

        merged = False
        protected = _is_protected_path(file_path)
        if protected:
            yield {
                "type": "info",
                "content": "Protected path — auto-merge disabled. Manual review required.",
            }
        elif auto_merge:
            yield {"type": "step", "content": f"Merging PR #{pr.number}…"}
            merged = await gh.merge_pull_request(pr.number, commit_title=pr_title)
            if merged:
                yield {"type": "step", "content": "PR merged — Argo CD will sync from main."}
            else:
                yield {
                    "type": "info",
                    "content": "Auto-merge failed (branch protection?). Review PR manually.",
                }
        else:
            yield {
                "type": "info",
                "content": "GITOPS_AUTO_MERGE=false — approve and merge the PR manually.",
            }

        success = merged or (not auto_merge and not protected)
        summary = (
            f"## GitOps Fix Result\n\n"
            f"**Status:** {'SUCCESS' if success else 'PARTIAL'}\n\n"
            f"**Repository:** {repo_label}\n"
            f"**Branch:** `{fix_branch}`\n"
            f"**File:** `{file_path}`\n"
            f"**Pull Request:** {pr.html_url}\n"
            f"**Merged:** {'yes' if merged else 'no (awaiting review)'}\n\n"
            f"### Change\n{change_summary}\n"
        )
        yield {
            "type": "done",
            "success": success,
            "output": summary,
            "pr_url": pr.html_url,
            "merged": merged,
        }
    except Exception as exc:
        logger.exception("GitOps fix workflow failed")
        yield {"type": "error", "content": str(exc)}
