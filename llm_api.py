"""OpenRouter chat API client."""

from __future__ import annotations

import json
import os

import requests
from pydantic import BaseModel

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"


def _chat(payload: dict) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY가 필요합니다.")

    base_url = os.getenv("OPENROUTER_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def chat_completion(messages: list[dict[str, str]]) -> str:
    return _chat(
        {
            "model": os.getenv("OPENROUTER_MODEL", _DEFAULT_MODEL),
            "messages": messages,
            "temperature": 0,
        }
    )


def chat_structured(
    *,
    system_prompt: str,
    user_content: str,
    response_model: type[BaseModel],
) -> BaseModel:
    schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
    content = _chat(
        {
            "model": os.getenv("OPENROUTER_MODEL", _DEFAULT_MODEL),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{system_prompt}\n\n"
                        "Respond with JSON only.\n"
                        f"Schema:\n{schema}"
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
    )
    return response_model.model_validate(json.loads(content))
