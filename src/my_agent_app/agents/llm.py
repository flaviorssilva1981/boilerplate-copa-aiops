"""LLM configuration for LangChain agents via the Requesty AI gateway."""

import os

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI


def _get_max_tokens() -> int:
    raw = os.environ.get("AGENT_MAX_TOKENS", "8192")
    try:
        value = int(raw)
    except ValueError:
        value = 0
    return value if value > 0 else 8192


def get_agent_llm() -> BaseChatModel:
    """Return the agent LLM (Requesty OpenAI-compatible gateway, any provider model)."""
    return ChatOpenAI(
        model=os.environ.get("AGENT_MODEL_NAME", "anthropic/claude-sonnet-5"),
        api_key=os.environ["REQUESTY_API_KEY"],
        base_url=os.environ.get("REQUESTY_BASE_URL", "https://router.requesty.ai/v1"),
        max_tokens=_get_max_tokens(),
        max_retries=3,
        timeout=120,
    )
