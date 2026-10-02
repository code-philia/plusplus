from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class DatabasePassSpec:
    phase: str
    schema_name: str
    output_schema: dict[str, Any]
    instructions: str
    success_state: str
    skip_if: Callable[[str, Any], bool] | None = None


class DatabaseDesignPass:
    """Reusable adapter for one deterministic database-design phase."""

    spec: DatabasePassSpec

    def run(
        self,
        *,
        execute: Callable[..., dict[str, Any] | None],
        apply_decision: Callable[[str, dict[str, Any], list[str]], None],
        node_id: str,
        node: dict[str, Any],
        context: dict[str, Any],
        errors: list[str],
        warnings: list[str],
        state: Any,
    ) -> str:
        if self.spec.skip_if and self.spec.skip_if(node_id, state):
            return "SKIPPED"
        decision = execute(
            phase=self.spec.phase,
            schema_name=self.spec.schema_name,
            output_schema=self.spec.output_schema,
            instructions=self.spec.instructions,
            node_id=node_id,
            context=context,
            errors=errors,
            warnings=warnings,
        )
        if decision is None:
            return "FAILED"
        error_count = len(errors)
        apply_decision(node_id, decision, errors)
        if len(errors) != error_count:
            return "FAILED"
        return self.spec.success_state


__all__ = ["DatabaseDesignPass", "DatabasePassSpec"]