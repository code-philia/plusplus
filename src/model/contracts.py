"""Technology-independent structured model interface."""
from __future__ import annotations

from typing import Any, Protocol


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


