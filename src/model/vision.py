"""Multimodal structured-output transport."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
from openai import OpenAI
from .logging import log_design_call, logged_completion
from .client import completion_request_kwargs, record_model_usage, response_format_unavailable


class VisualModelConfigurationError(RuntimeError):
    """Raised when neither visual nor main model configuration is usable."""


class VisualModel:
    """OpenAI-compatible multimodal structured-output client."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 120.0,
        transport_retries: int = 3,
        usage_path: Path | None = None,
    ) -> None:
        if not model.strip():
            raise VisualModelConfigurationError(
                "VISUAL_MODEL or MODEL is required for visual-reference analysis."
            )
        if not api_key.strip():
            raise VisualModelConfigurationError(
                "VISUAL_API_KEY or OPENAI_API_KEY is required for visual-reference analysis."
            )
        self.model = model.strip()
        self._usage_path = usage_path.expanduser().resolve() if usage_path is not None else None
        self._structured_output_mode = "json_schema"
        self._client = OpenAI(
            api_key=api_key.strip(),
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            max_retries=max(0, transport_retries),
        )

    @property
    def structured_output_mode(self) -> str:
        """Expose provider fallback for generation attempt diagnostics."""
        return getattr(self, "_last_output_mode", self._structured_output_mode)

    def set_usage_path(self, path: Path | None) -> None:
        self._usage_path = path.expanduser().resolve() if path is not None else None

    @classmethod
    def from_env(cls) -> "VisualModel":
        env_file = os.environ.get("ARC_ENV_FILE", "").strip()
        load_dotenv(env_file or ".env", override=False, encoding="utf-8-sig")
        return cls(
            model=os.environ.get("VISUAL_MODEL", "").strip()
            or os.environ.get("MODEL", "").strip(),
            api_key=os.environ.get("VISUAL_API_KEY", "").strip()
            or os.environ.get("OPENAI_API_KEY", "").strip(),
            base_url=os.environ.get("VISUAL_BASE_URL", "").strip()
            or os.environ.get("OPENAI_BASE_URL", "").strip()
            or "https://api.openai.com/v1",
            timeout_seconds=_positive_env_float("ARC_VISUAL_TIMEOUT_SECONDS", 120.0),
            transport_retries=_nonnegative_env_int("ARC_MODEL_TRANSPORT_RETRIES", 3),
        )

    @log_design_call
    def generate_visual_json(
        self,
        *,
        schema_name: str,
        instructions: str,
        input_payload: dict[str, Any],
        image_data_url: str,
        output_schema: dict[str, Any],
    ) -> dict[str, Any]:
        output_schema = dict(output_schema)
        force_json = output_schema.pop("x-arc-output-mode", None) == "json_object"
        self._last_output_mode = "json_object" if force_json else self._structured_output_mode
        messages = _visual_messages(instructions, input_payload, image_data_url, schema_name=schema_name)
        response = None
        if not force_json and self._structured_output_mode == "json_schema":
            try:
                response = logged_completion(self._client,
                    **completion_request_kwargs(
                        model=self.model,
                        messages=messages,
                        response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema_name,
                            "strict": True,
                            "schema": output_schema,
                        },
                        },
                    )
                )
                record_model_usage(self._usage_path, response, schema_name=schema_name,
                                   operation="generate_visual_json", model=self.model)
            except Exception as exc:
                if not response_format_unavailable(exc):
                    raise
                # Capability negotiation is cached for this compiler run. The
                # pass is synchronous, so later images can skip the known-bad
                # request without introducing shared-state races.
                self._structured_output_mode = "json_object"
                self._last_output_mode = "json_object"

        fallback_instructions = (
            f"{instructions.rstrip()}\n\n"
            "Return exactly one JSON object matching this JSON Schema; include every required "
            "property; optional properties may be omitted. Additional properties are allowed only where "
            "the schema permits them (such as literal JSON objects). Do not use Markdown:\n"
            f"{json.dumps(output_schema, ensure_ascii=False, separators=(',', ':'))}"
        )
        fallback_messages = _visual_messages(
            fallback_instructions,
            input_payload,
            image_data_url,
            schema_name=schema_name,
        )
        if response is None and (force_json or self._structured_output_mode == "json_object"):
            try:
                response = logged_completion(self._client,
                    **completion_request_kwargs(
                        model=self.model,
                        messages=fallback_messages,
                        response_format={"type": "json_object"},
                    )
                )
                record_model_usage(self._usage_path, response, schema_name=schema_name,
                                   operation="generate_visual_json", model=self.model)
            except Exception as exc:
                if not response_format_unavailable(exc):
                    raise
                if force_json:
                    raise VisualModelConfigurationError("Frontend protocol requires JSON-object output support; no prompt-only fallback.") from exc
                self._structured_output_mode = "prompt_only"
                self._last_output_mode = "prompt_only"

        if response is None:
            response = logged_completion(self._client,
                **completion_request_kwargs(
                    model=self.model,
                    messages=fallback_messages,
                )
            )
            record_model_usage(self._usage_path, response, schema_name=schema_name,
                               operation="generate_visual_json", model=self.model)
        content = response.choices[0].message.content
        if content is None:
            raise ValueError("Visual structured model response is empty.")
        value = json.loads(str(content))
        if not isinstance(value, dict):
            raise ValueError("Visual structured model response must be a JSON object.")
        return value


def _visual_messages(
    instructions: str,
    input_payload: dict[str, Any],
    image_data_url: str,
    *, schema_name: str | None = None,
) -> list[dict[str, Any]]:
    from .messages import system_instructions, user_messages

    return [
        {"role": "system", "content": system_instructions(instructions)},
        *user_messages(input_payload, schema_name=schema_name),
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "## Reference image\nVisual evidence for this task. Follow the analysis instructions; do not infer additional business requirements.",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_url, "detail": "high"},
                },
            ],
        },
    ]


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
