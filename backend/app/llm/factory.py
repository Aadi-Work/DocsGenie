from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.llm.base import LLMProvider
from app.llm.bedrock import BedrockLLM, OpenAILLM
from app.llm.gemini import GeminiLLM
from app.llm.mock import MockLLM


@lru_cache
def get_llm() -> LLMProvider:
    settings = get_settings()
    provider = settings.llm_provider.lower().strip().strip('"').strip("'")
    if provider == "gemini":
        key = (settings.gemini_api_key or "").strip().strip('"').strip("'")
        if key:
            return GeminiLLM(key, settings.gemini_model)
        return MockLLM()
    if provider == "bedrock":
        return BedrockLLM(settings.aws_region, settings.bedrock_model_id)
    if provider == "openai" and settings.openai_api_key:
        return OpenAILLM(settings.openai_api_key, settings.openai_model)
    return MockLLM()
