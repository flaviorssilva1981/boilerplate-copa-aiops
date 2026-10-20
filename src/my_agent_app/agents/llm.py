"""LLM configuration for LangChain agents via the OpenAI API (GPT-5)."""

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
    """Return the agent LLM (OpenAI API; base URL overridable for compatible gateways)."""
    return ChatOpenAI(
        model=os.environ.get("AGENT_MODEL_NAME", "gpt-5"),
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        max_tokens=_get_max_tokens(),
        max_retries=3,
        timeout=120,
    )
