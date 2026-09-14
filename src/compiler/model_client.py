from __future__ import annotations

import json
import os
from typing import Any, Protocol

import requests
from dotenv import load_dotenv


class StructuredModel(Protocol):
    """Small interface used by semantic compiler passes."""

    def generate_json(
        self,
        *,
        schema_name: str,
        instructions: str,
        input_payload: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Return one JSON object conforming to ``output_schema``."""


class ModelConfigurationError(RuntimeError):
    """Raised when a semantic pass has no usable model configuration."""


class OpenAIChatCompletionsModel:
    """Structured-output client for the OpenAI Chat Completions API."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 120.0,
    ) -> None:
        if not model.strip():
            raise ModelConfigurationError("MODEL is required for the DATABASE_SCHEMA pass.")
        if not api_key.strip():
            raise ModelConfigurationError("OPENAI_API_KEY is required for the DATABASE_SCHEMA pass.")
        self.model = model.strip()
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "OpenAIChatCompletionsModel":
        env_file = os.environ.get("ARC_ENV_FILE", "").strip()
        load_dotenv(env_file or ".env", override=False)
        return cls(
            model=os.environ.get("MODEL", ""),
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL", "").strip()
            or "https://api.openai.com/v1",
        )

    def generate_json(
        self,
        *,
        schema_name: str,
        instructions: str,
        input_payload: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> dict[str, Any]:
        user_input = json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_input},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": output_schema,
                },
            },
        }

        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        text = str(payload["choices"][0]["message"]["content"])
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("Structured model response must be a JSON object.")
        return parsed
