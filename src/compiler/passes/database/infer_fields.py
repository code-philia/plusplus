from __future__ import annotations

from typing import Any

from .base import DatabaseDesignPass, DatabasePassSpec


class InferFieldsPass(DatabaseDesignPass):
    """Database pass 2: infer fields for entities in each requirement slice."""

    def __init__(self, output_schema: dict[str, Any], instructions: str) -> None:
        self.spec = DatabasePassSpec(
            phase="pass2_fields",
            schema_name="arc_database_fields",
            output_schema=output_schema,
            instructions=instructions,
            success_state="FIELDS_DISCOVERED",
        )


__all__ = ["InferFieldsPass"]