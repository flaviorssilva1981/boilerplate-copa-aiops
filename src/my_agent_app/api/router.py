import asyncio
import json
import logging
import os
import ssl
from urllib.error import HTTPError
from urllib.request import Request as URLRequest
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage

from my_agent_app.agents.llm import get_agent_llm

logger = logging.getLogger(__name__)
router = APIRouter()

_SA_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_SA_CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
_K8S_API = "https://kubernetes.default.svc"


# ── Kubernetes quantity parsers ───────────────────────────────
def _parse_cpu_millis(val: str) -> float:
    """Parse Kubernetes CPU quantity to milliCPU (m).
    Handles: nanocores (n), milliCPU (m), whole cores, and kilo-cores (k).
    """
    val = val.strip()
    if val.endswith("n"):
        return float(val[:-1]) / 1_000_000  # nanocores → milliCPU
    if val.endswith("m"):
        return float(val[:-1])
    if val.endswith("k"):
        return float(val[:-1]) * 1_000_000
    return float(val) * 1000


def _parse_mem_bytes(val: str) -> int:
    val = val.strip()
    for sfx, mult in [
        ("Ki", 1024),
        ("Mi", 1024**2),
        ("Gi", 1024**3),
        ("Ti", 1024**4),
        ("k", 1000),
        ("M", 1000**2),
        ("G", 1000**3),
        ("T", 1000**4),
    ]:
        if val.endswith(sfx):
            return int(float(val[: -len(sfx)]) * mult)
    return int(val)


def _fmt_mem(b: int) -> str:
    for unit, div in [("Ti", 1024**4), ("Gi", 1024**3), ("Mi", 1024**2), ("Ki", 1024)]:
        if b >= div:
            return f"{b / div:.1f} {unit}"
    return f"{b} B"


def _k8s_get(path: str) -> dict:
    """Blocking call to the Kubernetes API using the pod service-account."""
    if not os.path.exists(_SA_TOKEN):
        raise RuntimeError("Not in-cluster (no service-account token)")
    with open(_SA_TOKEN) as f:
        token = f.read().strip()
    ctx = ssl.create_default_context(cafile=_SA_CA if os.path.exists(_SA_CA) else None)
    req = URLRequest(f"{_K8S_API}{path}", headers={"Authorization": f"Bearer {token}"})
    with urlopen(req, context=ctx, timeout=8) as resp:  # nosec B310
        return json.loads(resp.read())


@router.get("/api/health")
def health():
    return {"status": "ok"}


@router.get("/api/config")
def config():
    return {
        "llm_provider": "requesty",
        "anthropic_base_url": os.environ.get("ANTHROPIC_BASE_URL", "https://router.requesty.ai"),
        "agent_model": os.environ.get("AGENT_MODEL_NAME", "anthropic/claude-sonnet-4-5"),
        "mcp_server_url": os.environ.get("MCP_SERVER_URL"),
        "database_configured": bool(os.environ.get("DATABASE_URL")),
    }


@router.get("/api/health/cluster")
async def cluster_metrics():
    """Return live cluster resource metrics via the Kubernetes API (in-cluster service account)."""

    def _fetch_all() -> dict:
        # ── 1. Node metrics (metrics-server) ─────────────────────
        nodes_usage: dict[str, dict] = {}
        try:
            metrics = _k8s_get("/apis/metrics.k8s.io/v1beta1/nodes")
            for item in metrics.get("items", []):
                name = item["metadata"]["name"]
                usage = item.get("usage", {})
                nodes_usage[name] = {
                    "cpu_m": _parse_cpu_millis(usage.get("cpu", "0")),
                    "mem_b": _parse_mem_bytes(usage.get("memory", "0")),
                }
        except HTTPError as e:
            if e.code == 403:
                logger.warning(
                    "cluster_metrics: no permission for metrics.k8s.io (403) — add RBAC ClusterRole"
                )
            else:
                raise

        # ── 2. Node capacity ──────────────────────────────────────
        nodes_capacity: dict[str, dict] = {}
        nodes_data = _k8s_get("/api/v1/nodes")
        for item in nodes_data.get("items", []):
            name = item["metadata"]["name"]
            alloc = item.get("status", {}).get("allocatable", {})
            nodes_capacity[name] = {
                "cpu_m": _parse_cpu_millis(alloc.get("cpu", "1")),
                "mem_b": _parse_mem_bytes(alloc.get("memory", "1")),
                "pods": int(alloc.get("pods", "110")),
            }

        # ── 3. Build per-node rows ────────────────────────────────
        nodes: list[dict] = []
        total_cpu_used = total_cpu_alloc = 0.0
        total_mem_used = total_mem_alloc = 0.0
        total_pods_cap = 0

        for name, cap in nodes_capacity.items():
            usage = nodes_usage.get(name, {})
            cpu_used = usage.get("cpu_m", 0.0)
            mem_used = usage.get("mem_b", 0)
            cpu_pct = round(cpu_used / cap["cpu_m"] * 100, 1) if cap["cpu_m"] else 0
            mem_pct = round(mem_used / cap["mem_b"] * 100, 1) if cap["mem_b"] else 0
            nodes.append(
                {
                    "name": name,
                    "cpu": f"{int(cpu_used)}m" if cpu_used else "—",
                    "cpu_pct": cpu_pct if usage else None,
                    "mem": _fmt_mem(mem_used) if mem_used else "—",
                    "mem_pct": mem_pct if usage else None,
                }
            )
            total_cpu_used += cpu_used
            total_cpu_alloc += cap["cpu_m"]
            total_mem_used += mem_used
            total_mem_alloc += cap["mem_b"]
            total_pods_cap += cap["pods"]

        # ── 4. Pod counts ─────────────────────────────────────────
        by_phase: dict[str, int] = {}
        pods_data = _k8s_get("/api/v1/pods")
        for pod in pods_data.get("items", []):
            phase = (pod.get("status") or {}).get("phase", "Unknown")
            by_phase[phase] = by_phase.get(phase, 0) + 1

        pods_total = sum(by_phase.values())
        pods_running = by_phase.get("Running", 0)

        has_usage = bool(nodes_usage)
        return {
            "cpu_pct": (
                round(total_cpu_used / total_cpu_alloc * 100, 1)
                if has_usage and total_cpu_alloc
                else None
            ),
            "mem_pct": (
                round(total_mem_used / total_mem_alloc * 100, 1)
                if has_usage and total_mem_alloc
                else None
            ),
            "cpu_detail": (
                f"{int(total_cpu_used)}m / {int(total_cpu_alloc)}m" if has_usage else None
            ),
            "mem_detail": (
                f"{_fmt_mem(int(total_mem_used))} / {_fmt_mem(int(total_mem_alloc))}"
                if has_usage
                else None
            ),
            "pods_total": pods_total,
            "pods_running": pods_running,
            "pods_cap": total_pods_cap,
            "pods_by_phase": by_phase,
            "nodes": nodes,
            "error": None,
        }

    try:
        return await asyncio.wait_for(asyncio.to_thread(_fetch_all), timeout=12)
    except TimeoutError:
        return {"error": "Kubernetes API request timed out", "nodes": [], "pods_by_phase": {}}
    except RuntimeError as exc:
        return {"error": str(exc), "nodes": [], "pods_by_phase": {}}
    except Exception as exc:
        logger.warning("cluster_metrics failed: %s", exc)
        return {"error": str(exc), "nodes": [], "pods_by_phase": {}}


@router.post("/api/agent/ping")
async def agent_ping():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not configured")

    llm = get_agent_llm()
    response = await llm.ainvoke([HumanMessage(content="Reply with exactly: AIOps agent online.")])
    return {
        "status": "ok",
        "model": os.environ.get("AGENT_MODEL_NAME", "anthropic/claude-sonnet-4-5"),
        "reply": response.content,
    }
