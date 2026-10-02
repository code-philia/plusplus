from __future__ import annotations

from typing import Any

from .base import DatabaseDesignPass, DatabasePassSpec


class InferRelationsPass(DatabaseDesignPass):
    """Database pass 3: infer relationships between discovered entities."""

    def __init__(self, output_schema: dict[str, Any], instructions: str) -> None:
        self.spec = DatabasePassSpec(
            phase="pass3_relationships",
            schema_name="arc_database_relationships",
            output_schema=output_schema,
            instructions=instructions,
            success_state="RELATIONSHIPS_RESOLVED",
            skip_if=lambda _node_id, state: len(state.related_entity_keys(_node_id)) < 2,
        )


__all__ = ["InferRelationsPass"]