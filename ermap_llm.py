"""LLM client factory for OpenAI and OpenRouter."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from langchain_openai import ChatOpenAI

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT_SEC = 60
DEFAULT_MAX_RETRIES = 2


def resolve_llm_settings() -> Dict[str, Any]:
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        return {
            "provider": "openrouter",
            "model": os.getenv("ERMAP_LLM_MODEL", DEFAULT_OPENROUTER_MODEL),
            "api_key": openrouter_key,
            "base_url": os.getenv("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL),
            "default_headers": {
                "HTTP-Referer": os.getenv(
                    "OPENROUTER_HTTP_REFERER",
                    "https://github.com/dev-strzX9/dev",
                ),
                "X-Title": os.getenv("OPENROUTER_APP_TITLE", "ER MAP Agent"),
            },
        }

    return {
        "provider": "openai",
        "model": os.getenv("ERMAP_LLM_MODEL", DEFAULT_MODEL),
        "api_key": os.getenv("OPENAI_API_KEY"),
        "base_url": os.getenv("OPENAI_BASE_URL"),
        "default_headers": None,
    }


def get_llm_config_summary() -> str:
    settings = resolve_llm_settings()
    return (
        f"provider={settings['provider']} "
        f"model={settings['model']} "
        f"base_url={settings.get('base_url') or 'default'}"
    )


def create_chat_llm(
    *,
    temperature: float = 0,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    max_retries: int = DEFAULT_MAX_RETRIES,
    model: Optional[str] = None,
) -> ChatOpenAI:
    settings = resolve_llm_settings()
    kwargs: Dict[str, Any] = {
        "model": model or settings["model"],
        "temperature": temperature,
        "timeout": timeout,
        "max_retries": max_retries,
    }

    if settings.get("api_key"):
        kwargs["api_key"] = settings["api_key"]
    if settings.get("base_url"):
        kwargs["base_url"] = settings["base_url"]
    if settings.get("default_headers"):
        kwargs["default_headers"] = settings["default_headers"]

    return ChatOpenAI(**kwargs)
