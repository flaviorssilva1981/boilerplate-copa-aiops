"""LLM configuration for LangChain agents via Requesty gateway."""

import os

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI


def get_agent_llm() -> BaseChatModel:
    """Return the agent LLM (Requesty OpenAI-compatible API, any provider model)."""
    return ChatOpenAI(
        model=os.environ.get("AGENT_MODEL_NAME", "google/gemini-2.5-pro"),
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://router.requesty.ai/v1"),
        max_retries=3,
    )
