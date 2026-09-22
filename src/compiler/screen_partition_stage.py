"""Partition each frozen screen into the writable components that own requirements."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.logging import SynchronousLog

from .frontend_ir import FrontendDesignErrorCode, FrontendDesignIssue
from .frontend_thin_design import validate_screen_components
from .frontend_thin_ir import SCREEN_COMPONENT_SCHEMA, repair_shape
from .model_client import StructuredModel, describe_model_error
from .trace_payload import format_payload_trace


SCREEN_PARTITION_INSTRUCTIONS = """Partition one already frozen screen into the components that implement it.
A component is the unit a single requirement is implemented in: one cohesive region of the screen with its own data,
its own local state, and its own interactions. Return the smallest set of components that satisfies these rules.

Every requirement the screen serves belongs to exactly one component. Every API the screen declares belongs to exactly
one component. Every observable state the screen declares belongs to exactly one component. Never assign the same
requirement, API, or observable state to two components, and never leave one unassigned.

A component is self-sufficient: it calls its own APIs, reads and writes its own shared state, and renders its own
observable states. The page only composes components, so never plan a component that depends on data another component
must hand to it. Two requirements that read and write the same region of the screen belong in the same component; two
requirements that a user can operate independently belong in different components.

Component ids are COMPONENT.<PascalName> and must be unique across the whole product; include the screen name in the id
when a generic name would collide. Use only the requirement ids, API ids, shared state ids, observable states, and
visual reference ids supplied in the context. Copy route input field objects exactly, and list only the route inputs the
component itself needs. Return exactly one JSON object and no prose."""

SCREEN_PARTITION_DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["components"],
    "properties": {
        "components": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    key
                    for key in SCREEN_COMPONENT_SCHEMA["required"]
                    if key != "screen_id"
                ],
                "properties": {
                    key: copy.deepcopy(value)
                    for key, value in SCREEN_COMPONENT_SCHEMA["properties"].items()
                    if key != "screen_id"
                },
            },
        }
    },
}


@dataclass(slots=True)
class ScreenPartitionResult:
    frontend_ir: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class ScreenPartitionPass:
    """Turn every screen into a composition of independently writable components."""

    def __init__(self, model: StructuredModel, artifact_root: Path) -> None:
        self._model = model
        self._log = SynchronousLog(
            "ScreenPartitionPass", workspace_root=artifact_root.resolve().parent
        )

    def compile(
        self,
        frontend_ir: dict[str, Any],
        requirement_ir: dict[str, Any],
    ) -> ScreenPartitionResult:
        result = copy.deepcopy(frontend_ir)
        nodes = requirement_ir.get("nodes", {})
        components: list[dict[str, Any]] = []
        errors: list[str] = []
        for screen in result.get("screens", []):
            if not isinstance(screen, dict) or not screen.get("requirement_ids"):
                continue
            screen_id = str(screen["id"])
            planned, issues = self._partition_screen(
                screen, result, nodes, {str(row["id"]) for row in components}
            )
            if issues:
                errors.extend(row.format() for row in issues)
                continue
            self._log.info(
                f"SCREEN_PARTITIONED screen={screen_id} "
                f"components={[str(row['id']) for row in planned]}"
            )
            components.extend(planned)
        result["screen_components"] = sorted(components, key=lambda row: str(row["id"]))
        if errors:
            return ScreenPartitionResult({}, errors)
        issues = validate_screen_components(result)
        if issues:
            return ScreenPartitionResult({}, [row.format() for row in issues])
        return ScreenPartitionResult(result)

    def _partition_screen(
        self,
        screen: dict[str, Any],
        frontend_ir: dict[str, Any],
        nodes: dict[str, Any],
        taken_ids: set[str],
    ) -> tuple[list[dict[str, Any]], list[FrontendDesignIssue]]:
        screen_id = str(screen["id"])
        payload = _screen_context(screen, frontend_ir, nodes)
        feedback: list[str] = []
        issues: list[FrontendDesignIssue] = []
        for attempt in range(1, 4):
            request = {**payload, **({"validation_feedback": feedback} if feedback else {})}
            self._log.info(
                f"MODEL_REQUEST phase=screen_partition screen={screen_id} attempt={attempt}/3"
            )
            started = time.perf_counter()
            try:
                raw = self._model.generate_json(
                    schema_name="arc_screen_partition",
                    instructions=SCREEN_PARTITION_INSTRUCTIONS,
                    input_payload=request,
                    output_schema=_provider_schema(SCREEN_PARTITION_DECISION_SCHEMA),
                )
            except Exception as exc:
                feedback = [f"Screen partition model call failed: {describe_model_error(exc)}"]
                issues = [_issue(
                    FrontendDesignErrorCode.COMPONENT_MODEL_FAILED,
                    f"Screen {screen_id}: {feedback[0]}",
                )]
                continue
            self._log.info(
                f"MODEL_OUTPUT phase=screen_partition screen={screen_id} "
                f"duration_ms={int((time.perf_counter() - started) * 1000)}\n"
                + format_payload_trace(raw)
            )
            decision = repair_shape(raw, SCREEN_PARTITION_DECISION_SCHEMA)
            rows = decision.get("components", []) if isinstance(decision, dict) else []
            planned = [
                {**copy.deepcopy(row), "screen_id": screen_id}
                for row in rows
                if isinstance(row, dict)
            ]
            issues = _decision_issues(planned, screen, frontend_ir, taken_ids)
            if not issues:
                return planned, []
            feedback = [row.format() for row in issues]
        return [], issues or [_issue(
            FrontendDesignErrorCode.COMPONENT_MODEL_FAILED,
            f"Screen {screen_id} was not partitioned into components.",
        )]


def _decision_issues(
    planned: list[dict[str, Any]],
    screen: dict[str, Any],
    frontend_ir: dict[str, Any],
    taken_ids: set[str],
) -> list[FrontendDesignIssue]:
    """Validate one screen's partition against the frozen screen contract."""

    if not planned:
        return [_issue(
            FrontendDesignErrorCode.COMPONENT_DECISION_INVALID,
            f"Screen {screen['id']} returned no component.",
        )]
    issues = [
        _issue(
            FrontendDesignErrorCode.SYMBOL_DUPLICATE,
            f"Component id {row['id']} is already used by another screen.",
        )
        for row in planned
        if str(row.get("id", "")) in taken_ids
    ]
    issues.extend(validate_screen_components({
        "screens": [copy.deepcopy(screen)],
        "screen_components": planned,
        "shared_state_policies": copy.deepcopy(
            frontend_ir.get("shared_state_policies", [])
        ),
    }))
    return issues


def _screen_context(
    screen: dict[str, Any],
    frontend_ir: dict[str, Any],
    nodes: dict[str, Any],
) -> dict[str, Any]:
    requirement_ids = [str(value) for value in screen.get("requirement_ids", [])]
    owned = set(requirement_ids)
    visual_ids = {str(value) for value in screen.get("visual_reference_ids", [])}
    return {
        "screen": copy.deepcopy(screen),
        "requirements": [
            {"requirement_id": rid, **copy.deepcopy(nodes.get(rid, {}))}
            for rid in requirement_ids
        ],
        "journeys": [
            copy.deepcopy(row)
            for row in frontend_ir.get("journeys", [])
            if isinstance(row, dict) and str(row.get("source_screen_id", "")) == str(screen["id"])
        ],
        "api_usages": [
            copy.deepcopy(row)
            for row in frontend_ir.get("api_usages", [])
            if isinstance(row, dict) and str(row.get("screen_id", "")) == str(screen["id"])
        ],
        "shared_state_policies": [
            copy.deepcopy(row)
            for row in frontend_ir.get("shared_state_policies", [])
            if isinstance(row, dict) and owned & {str(value) for value in row.get("requirement_ids", [])}
        ],
        "visual_references": [
            {"id": row.get("id"), "analysis": row.get("analysis", {})}
            for row in frontend_ir.get("visual_references", [])
            if isinstance(row, dict) and str(row.get("id", "")) in visual_ids
        ],
    }


def _issue(code: FrontendDesignErrorCode, message: str) -> FrontendDesignIssue:
    return FrontendDesignIssue(code, message, "SCREEN_PARTITION", "FRONTEND")


def _provider_schema(schema: dict[str, Any]) -> dict[str, Any]:
    unsupported = {"maxLength", "uniqueItems"}
    return {
        key: _provider_schema(value)
        if isinstance(value, dict)
        else [_provider_schema(row) if isinstance(row, dict) else row for row in value]
        if isinstance(value, list)
        else value
        for key, value in schema.items()
        if key not in unsupported
    }
