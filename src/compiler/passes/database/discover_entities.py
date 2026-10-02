from __future__ import annotations

from typing import Any

from .base import DatabaseDesignPass, DatabasePassSpec


class DiscoverEntitiesPass(DatabaseDesignPass):
    """Database pass 1: discover or reuse persistent entities."""

    def __init__(self, output_schema: dict[str, Any], instructions: str) -> None:
        self.spec = DatabasePassSpec(
            phase="pass1_entities",
            schema_name="arc_database_entities",
            output_schema=output_schema,
            instructions=instructions,
            success_state="ENTITIES_DISCOVERED",
        )


__all__ = ["DiscoverEntitiesPass"]