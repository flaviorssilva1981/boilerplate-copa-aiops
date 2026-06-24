"""Async GitHub REST API client for GitOps fix workflow."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

import httpx


@dataclass
class GitHubFile:
    path: str
    content: str
    sha: str


@dataclass
class PullRequest:
    number: int
    html_url: str
    state: str


class GitHubClient:
    def __init__(self, token: str, owner: str, repo: str) -> None:
        self.owner = owner
        self.repo = repo
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def get_branch_sha(self, branch: str) -> str:
        async with httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=self._headers,
            timeout=60.0,
        ) as client:
            response = await client.get(f"/repos/{self.owner}/{self.repo}/git/ref/heads/{branch}")
            response.raise_for_status()
            return response.json()["object"]["sha"]

    async def create_branch(self, branch: str, from_sha: str) -> None:
        async with httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=self._headers,
            timeout=60.0,
        ) as client:
            response = await client.post(
                f"/repos/{self.owner}/{self.repo}/git/refs",
                json={"ref": f"refs/heads/{branch}", "sha": from_sha},
            )
            if response.status_code == 422 and "Reference already exists" in response.text:
                return
            response.raise_for_status()

    async def get_file(self, path: str, ref: str) -> GitHubFile:
        async with httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=self._headers,
            timeout=60.0,
        ) as client:
            response = await client.get(
                f"/repos/{self.owner}/{self.repo}/contents/{path}",
                params={"ref": ref},
            )
            response.raise_for_status()
            data = response.json()
            raw = base64.b64decode(data["content"]).decode("utf-8")
            return GitHubFile(path=path, content=raw, sha=data["sha"])

    async def update_file(
        self,
        path: str,
        content: str,
        message: str,
        branch: str,
        sha: str,
    ) -> str:
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        async with httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=self._headers,
            timeout=60.0,
        ) as client:
            response = await client.put(
                f"/repos/{self.owner}/{self.repo}/contents/{path}",
                json={
                    "message": message,
                    "content": encoded,
                    "branch": branch,
                    "sha": sha,
                },
            )
            response.raise_for_status()
            return response.json()["commit"]["sha"]

    async def create_pull_request(
        self,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> PullRequest:
        async with httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=self._headers,
            timeout=60.0,
        ) as client:
            response = await client.post(
                f"/repos/{self.owner}/{self.repo}/pulls",
                json={"title": title, "body": body, "head": head, "base": base},
            )
            if response.status_code == 422:
                existing = await client.get(
                    f"/repos/{self.owner}/{self.repo}/pulls",
                    params={"head": f"{self.owner}:{head}", "base": base, "state": "open"},
                )
                existing.raise_for_status()
                pulls = existing.json()
                if pulls:
                    pr = pulls[0]
                    return PullRequest(
                        number=pr["number"], html_url=pr["html_url"], state=pr["state"]
                    )
            response.raise_for_status()
            data = response.json()
            return PullRequest(
                number=data["number"],
                html_url=data["html_url"],
                state=data["state"],
            )

    async def merge_pull_request(
        self,
        pull_number: int,
        commit_title: str,
        merge_method: str = "squash",
    ) -> bool:
        async with httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=self._headers,
            timeout=60.0,
        ) as client:
            response = await client.put(
                f"/repos/{self.owner}/{self.repo}/pulls/{pull_number}/merge",
                json={
                    "commit_title": commit_title,
                    "merge_method": merge_method,
                },
            )
            if response.status_code == 405:
                return False
            response.raise_for_status()
            return bool(response.json().get("merged"))


def parse_gitops_repo() -> tuple[str, str]:
    repo = os.environ.get("GITOPS_REPO", "flaviorssilva1981/guiadodevops").strip()
    if repo.startswith("https://github.com/"):
        repo = repo.removeprefix("https://github.com/").rstrip("/")
    if "/" not in repo:
        raise ValueError(f"Invalid GITOPS_REPO: {repo!r} (expected owner/repo)")
    owner, name = repo.split("/", 1)
    return owner, name


def gitops_configured() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN"))
