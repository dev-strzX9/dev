"""OpenRouter chat API client using requests."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Type, TypeVar

import requests
from pydantic import BaseModel

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
DEFAULT_TIMEOUT_SEC = 120
DEFAULT_MAX_RETRIES = 2

TModel = TypeVar("TModel", bound=BaseModel)


def _require_api_key() -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY 환경 변수가 필요합니다.")
    return api_key


def _model() -> str:
    return os.getenv("OPENROUTER_MODEL", OPENROUTER_MODEL)


def _base_url() -> str:
    return os.getenv("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL).rstrip("/")


def _headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv(
            "OPENROUTER_HTTP_REFERER",
            "https://github.com/dev-strzX9/dev",
        ),
        "X-Title": os.getenv("OPENROUTER_APP_TITLE", "ER MAP Agent"),
    }


def get_llm_config_summary() -> str:
    return f"openrouter model={_model()} base_url={_base_url()}"


def _chat_url() -> str:
    return f"{_base_url()}/chat/completions"


def _extract_content(response_json: Dict[str, Any]) -> str:
    try:
        content = response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected OpenRouter response: {response_json}") from exc

    if not content:
        raise RuntimeError("Empty OpenRouter response content")
    return content


def _post_chat(payload: Dict[str, Any], *, timeout: int, max_retries: int) -> Dict[str, Any]:
    api_key = _require_api_key()
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                _chat_url(),
                headers=_headers(api_key),
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            time.sleep(2**attempt)

    raise RuntimeError(f"OpenRouter request failed after retries: {last_error}") from last_error


def chat_completion(
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> str:
    payload = {
        "model": _model(),
        "messages": messages,
        "temperature": temperature,
    }
    return _extract_content(_post_chat(payload, timeout=timeout, max_retries=max_retries))


def chat_structured(
    *,
    system_prompt: str,
    user_content: str,
    response_model: Type[TModel],
    temperature: float = 0,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> TModel:
    schema = response_model.model_json_schema()
    structured_system_prompt = (
        f"{system_prompt}\n\n"
        "Respond with a single JSON object only. No markdown fences.\n"
        f"The JSON must validate against this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )

    payload: Dict[str, Any] = {
        "model": _model(),
        "messages": [
            {"role": "system", "content": structured_system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }

    try:
        content = _extract_content(
            _post_chat(payload, timeout=timeout, max_retries=max_retries)
        )
        return response_model.model_validate(json.loads(content))
    except (RuntimeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            f"OpenRouter structured output failed: {exc}"
        ) from exc
