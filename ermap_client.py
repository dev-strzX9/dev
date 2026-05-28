"""HTTP client for OpenAI chat completions."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Type

import requests
from pydantic import BaseModel


class OpenAIChatClient:
    """Minimal requests-based OpenAI chat completion client."""

    def __init__(self, api_key: str | None = None, timeout: int = 30) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.timeout = timeout
        self.url = "https://api.openai.com/v1/chat/completions"

        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required")

    def parse(
        self,
        *,
        model: str,
        messages: List[Dict[str, str]],
        response_model: Type[BaseModel],
        temperature: float = 0,
    ) -> BaseModel:
        schema = (
            response_model.model_json_schema()
            if hasattr(response_model, "model_json_schema")
            else response_model.schema()
        )
        payload: Dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "schema": schema,
                },
            },
        }

        response = requests.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(
                f"OpenAI request failed: {response.status_code} {response.text}"
            ) from exc

        content = response.json()["choices"][0]["message"]["content"]
        if hasattr(response_model, "model_validate_json"):
            return response_model.model_validate_json(content)
        return response_model.parse_raw(content)
