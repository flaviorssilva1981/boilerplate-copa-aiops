"""LLM configuration for LangChain agents via the native Anthropic API."""

import os

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel


def _get_max_tokens() -> int:
    raw = os.environ.get("AGENT_MAX_TOKENS", "8192")
    try:
        value = int(raw)
    except ValueError:
        value = 0
    return value if value > 0 else 8192


def get_agent_llm() -> BaseChatModel:
    """Return the agent LLM backed by the native Anthropic Messages API."""
    return ChatAnthropic(
        model=os.environ.get("AGENT_MODEL_NAME", "claude-sonnet-5"),
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        max_tokens=_get_max_tokens(),
        max_retries=3,
        timeout=120,
    )
