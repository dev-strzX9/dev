"""OpenAI-compatible chat API client using requests."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Type, TypeVar

import requests
from pydantic import BaseModel

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SEC = 120
DEFAULT_MAX_RETRIES = 2

TModel = TypeVar("TModel", bound=BaseModel)


def resolve_llm_settings() -> Dict[str, Any]:
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        return {
            "provider": "openrouter",
            "model": os.getenv("ERMAP_LLM_MODEL", DEFAULT_OPENROUTER_MODEL),
            "api_key": openrouter_key,
            "base_url": os.getenv("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL).rstrip("/"),
            "extra_headers": {
                "HTTP-Referer": os.getenv(
                    "OPENROUTER_HTTP_REFERER",
                    "https://github.com/dev-strzX9/dev",
                ),
                "X-Title": os.getenv("OPENROUTER_APP_TITLE", "ER MAP Agent"),
            },
        }

    base_url = os.getenv("OPENAI_BASE_URL", OPENAI_BASE_URL).rstrip("/")
    return {
        "provider": "openai",
        "model": os.getenv("ERMAP_LLM_MODEL", DEFAULT_MODEL),
        "api_key": os.getenv("OPENAI_API_KEY"),
        "base_url": base_url,
        "extra_headers": {},
    }


def get_llm_config_summary() -> str:
    settings = resolve_llm_settings()
    return (
        f"provider={settings['provider']} "
        f"model={settings['model']} "
        f"base_url={settings['base_url']}"
    )


def _build_headers(settings: Dict[str, Any]) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {settings['api_key']}",
        "Content-Type": "application/json",
    }
    headers.update(settings.get("extra_headers") or {})
    return headers


def _chat_completions_url(settings: Dict[str, Any]) -> str:
    return f"{settings['base_url']}/chat/completions"


def _extract_message_content(response_json: Dict[str, Any]) -> str:
    try:
        content = response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected chat completion response: {response_json}") from exc

    if not content:
        raise RuntimeError("Empty LLM response content")
    return content


def chat_completion(
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    max_retries: int = DEFAULT_MAX_RETRIES,
    model: Optional[str] = None,
) -> str:
    settings = resolve_llm_settings()
    if not settings.get("api_key"):
        raise RuntimeError("OPENROUTER_API_KEY or OPENAI_API_KEY is required")

    payload = {
        "model": model or settings["model"],
        "messages": messages,
        "temperature": temperature,
    }

    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                _chat_completions_url(settings),
                headers=_build_headers(settings),
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            return _extract_message_content(response.json())
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            time.sleep(2**attempt)

    raise RuntimeError(f"LLM chat completion failed after retries: {last_error}") from last_error


def chat_structured(
    *,
    system_prompt: str,
    user_content: str,
    response_model: Type[TModel],
    temperature: float = 0,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    max_retries: int = DEFAULT_MAX_RETRIES,
    model: Optional[str] = None,
) -> TModel:
    schema = response_model.model_json_schema()
    structured_system_prompt = (
        f"{system_prompt}\n\n"
        "Respond with a single JSON object only. No markdown fences.\n"
        f"The JSON must validate against this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )

    settings = resolve_llm_settings()
    if not settings.get("api_key"):
        raise RuntimeError("OPENROUTER_API_KEY or OPENAI_API_KEY is required")

    payload: Dict[str, Any] = {
        "model": model or settings["model"],
        "messages": [
            {"role": "system", "content": structured_system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }

    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                _chat_completions_url(settings),
                headers=_build_headers(settings),
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            content = _extract_message_content(response.json())
            data = json.loads(content)
            return response_model.model_validate(data)
        except (requests.RequestException, RuntimeError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            time.sleep(2**attempt)

    raise RuntimeError(f"LLM structured output failed after retries: {last_error}") from last_error
