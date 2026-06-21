"""LLM configuration for LangChain agents via Requesty gateway."""

import os

from langchain_anthropic import ChatAnthropic


def get_agent_llm() -> ChatAnthropic:
    return ChatAnthropic(
        model=os.environ.get("AGENT_MODEL_NAME", "anthropic/claude-sonnet-4-5"),
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://router.requesty.ai"),
        max_retries=3,
    )
