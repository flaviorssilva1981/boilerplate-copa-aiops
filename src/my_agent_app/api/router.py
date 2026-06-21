import os

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage

from my_agent_app.agents.llm import get_agent_llm

router = APIRouter()


@router.get("/api/health")
def health():
    return {"status": "ok"}


@router.get("/api/config")
def config():
    return {
        "llm_provider": "requesty",
        "anthropic_base_url": os.environ.get(
            "ANTHROPIC_BASE_URL", "https://router.requesty.ai"
        ),
        "agent_model": os.environ.get(
            "AGENT_MODEL_NAME", "anthropic/claude-sonnet-4-5"
        ),
        "mcp_server_url": os.environ.get("MCP_SERVER_URL"),
        "database_configured": bool(os.environ.get("DATABASE_URL")),
    }


@router.post("/api/agent/ping")
async def agent_ping():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not configured")

    llm = get_agent_llm()
    response = await llm.ainvoke(
        [HumanMessage(content="Reply with exactly: AIOps agent online.")]
    )
    return {
        "status": "ok",
        "model": os.environ.get("AGENT_MODEL_NAME", "anthropic/claude-sonnet-4-5"),
        "reply": response.content,
    }
