"""Technology-independent model protocols; implementations live in client and vision."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ModelToolCall:
    call_id: str
    name: str
    arguments: str
    kind: str = 'function'


@dataclass(frozen=True, slots=True)
class ModelToolTurn:
    calls: tuple[ModelToolCall, ...]
    assistant_message: dict[str, Any]
    raw_response: dict[str, Any]
    finish_reason: str
    content: str | None = None
    refusal: str | None = None


class ToolModel(Protocol):
    def generate_tool_turn(self, *, schema_name: str, instructions: str,
                           input_payload: dict[str, Any], tools: list[dict[str, Any]],
                           messages: list[dict[str, Any]]) -> ModelToolTurn: ...


class StructuredModel(Protocol):
    """Provider protocol; callers supply optional metadata through model.call_context.call_scope."""

    def generate_json(
        self,
        *,
        schema_name: str,
        instructions: str,
        input_payload: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Return one JSON object conforming to ``output_schema``."""
        ...


class VisualStructuredModel(Protocol):
    """Separate multimodal boundary; backend semantic passes remain text-only."""

    def generate_visual_json(
        self,
        *,
        schema_name: str,
        instructions: str,
        input_payload: dict[str, Any],
        image_data_url: str,
        output_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Return one structured decision derived from text metadata and an image."""
        ...
