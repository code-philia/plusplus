from __future__ import annotations

import json
import os
from typing import Any, Protocol

from dotenv import load_dotenv
from openai import OpenAI


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


def describe_model_error(error: BaseException) -> str:
    """Render the public exception and its underlying transport cause."""

    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(parts) < 8:
        seen.add(id(current))
        message = str(current).strip() or repr(current)
        parts.append(f"{type(current).__name__}: {message}")
        next_error = current.__cause__
        if next_error is None and not current.__suppress_context__:
            next_error = current.__context__
        current = next_error
    return " <- ".join(parts)


class Model:
    """Structured-output client backed by the configured chat-completions endpoint."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 120.0,
        transport_retries: int = 0,
    ) -> None:
        if not model.strip():
            raise ModelConfigurationError("MODEL is required for semantic compiler passes.")
        if not api_key.strip():
            raise ModelConfigurationError("OPENAI_API_KEY is required for semantic compiler passes.")
        self.model = model.strip()
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            max_retries=max(0, transport_retries),
        )

    @classmethod
    def from_env(cls) -> "Model":
        env_file = os.environ.get("ARC_ENV_FILE", "").strip()
        load_dotenv(env_file or ".env", override=False)
        return cls(
            model=os.environ.get("MODEL", ""),
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL", "").strip()
            or "https://api.openai.com/v1",
            timeout_seconds=_positive_env_float("ARC_MODEL_TIMEOUT_SECONDS", 120.0),
            transport_retries=_nonnegative_env_int("ARC_MODEL_TRANSPORT_RETRIES", 0),
        )

    def generate_json(
        self,
        *,
        schema_name: str,
        instructions: str,
        input_payload: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> dict[str, Any]:
        markdown_context = input_payload.get("context_markdown")
        user_input = (
            markdown_context
            if set(input_payload) == {"context_markdown"} and isinstance(markdown_context, str)
            else json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))
        )
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

        response = self._client.chat.completions.create(
            model=body["model"],
            stream=False,
            reasoning_effort="low",
            messages=body["messages"],
            response_format=body["response_format"],
        )
        content = response.choices[0].message.content
        if content is None:
            raise ValueError("Structured model response is empty.")
        text = str(content)
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("Structured model response must be a JSON object.")
        return parsed


def _positive_env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _nonnegative_env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default
