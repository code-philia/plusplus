"""One-pass thin Frontend Design and projection to compiler runtime seams."""

from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.logging import SynchronousLog

from .frontend_ir import FrontendDesignErrorCode, FrontendDesignIssue
from .frontend_thin_ir import FRONTEND_DESIGN_IR_SCHEMA, FRONTEND_IR_SCHEMA_VERSION, repair_shape, shape_errors
from .model_client import StructuredModel, describe_model_error


THIN_FRONTEND_INSTRUCTIONS = """Design the thin frontend contract for the entire product in one pass.
Return screens/routes, user journeys, API usages, cross-page state policies, and requirement links only.
Do not design layouts, components, JSX, CSS, files, props, events, component trees, or page-local state.
Reference images are evidence for each screen's eventual composition and visual language. Preserve their ids.
Every user-facing atomic requirement must link to at least one screen. Reuse one screen across requirements when it
represents the same route and product surface. Screen ids use PAGE.<PascalName>; shared state ids use STORE.<PascalName>;
journey ids use JOURNEY.<PascalName>. Routes and navigation targets are absolute. Only use supplied API and visual ids.
API usages describe semantic request/response bindings; use [] when no binding is needed. Shared state is only for
session or genuinely cross-page state; include only the public actions needed to update or clear it. Return exactly
one JSON object and no prose."""

THIN_FRONTEND_DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["screens", "journeys", "api_usages", "shared_state_policies", "requirement_links"],
    "properties": {
        key: copy.deepcopy(FRONTEND_DESIGN_IR_SCHEMA["properties"][key])
        for key in ("screens", "journeys", "api_usages", "shared_state_policies", "requirement_links")
    },
}


@dataclass(slots=True)
class ThinFrontendDesignResult:
    frontend_ir: dict[str, Any]
    node_states: dict[str, str]
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class ThinFrontendDesignPass:
    """Expose one small model interface for the whole connected frontend."""

    def __init__(self, model: StructuredModel, artifact_root: Path) -> None:
        self._model = model
        self._log = SynchronousLog("ThinFrontendDesignPass", workspace_root=artifact_root.resolve().parent)

    def compile(self, requirement_ir: dict[str, Any], dependency_graph: dict[str, Any], backend_design_ir: dict[str, Any], visual_references: list[dict[str, Any]]) -> ThinFrontendDesignResult:
        requirement_ids = {str(value) for value in requirement_ir.get("node_order", []) if str(value)} or {str(value) for value in requirement_ir.get("nodes", {})}
        payload = {
            "requirements": [{"requirement_id": rid, **copy.deepcopy(requirement_ir.get("nodes", {}).get(rid, {}))} for rid in sorted(requirement_ids)],
            "dependency_graph": copy.deepcopy(dependency_graph.get("atomic_dependencies", {})),
            "requirement_contracts": copy.deepcopy(backend_design_ir.get("requirements", [])),
            "backend_apis": [copy.deepcopy(row) for row in backend_design_ir.get("modules", []) if isinstance(row, dict) and row.get("kind") == "API"],
            "visual_references": [{"id": row.get("id"), "source_path": row.get("source_path"), "analysis": row.get("analysis", {})} for row in visual_references if isinstance(row, dict)],
        }
        api_ids = {str(row.get("id")) for row in backend_design_ir.get("modules", []) if isinstance(row, dict) and row.get("kind") == "API"}
        issues: list[FrontendDesignIssue] = []
        feedback: list[str] = []
        for attempt in range(1, 4):
            request = {**payload, **({"validation_feedback": feedback} if feedback else {})}
            request_envelope = {
                "instructions": THIN_FRONTEND_INSTRUCTIONS,
                "input_payload": request,
                "output_schema": _provider_schema(THIN_FRONTEND_DECISION_SCHEMA),
            }
            self._log.info(
                f"MODEL_REQUEST phase=thin_frontend_design attempt={attempt}/3"
            )
            self._log.info(
                _context_audit(
                    phase="thin_frontend_design",
                    attempt=attempt,
                    request_payload=request_envelope,
                )
            )
            started = time.perf_counter()
            try:
                raw = self._model.generate_json(
                    schema_name="arc_thin_frontend_design",
                    instructions=THIN_FRONTEND_INSTRUCTIONS,
                    input_payload=request,
                    output_schema=request_envelope["output_schema"],
                )
            except Exception as exc:
                feedback = [f"Frontend model call failed: {describe_model_error(exc)}"]
                continue
            self._log.info("MODEL_OUTPUT phase=thin_frontend_design " f"duration_ms={int((time.perf_counter() - started) * 1000)}\n" + json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True))
            decision = repair_shape(raw, THIN_FRONTEND_DECISION_SCHEMA)
            if not isinstance(decision, dict):
                feedback = ["Thin Frontend Design output must be one JSON object."]
                continue
            candidate = {
                "schema_version": FRONTEND_IR_SCHEMA_VERSION,
                "visual_references": copy.deepcopy(visual_references),
                **decision,
            }
            issues = validate_thin_frontend_design(candidate, expected_requirement_ids=requirement_ids, backend_api_ids=api_ids)
            if not issues:
                decision = _canonicalize(candidate)
                states = {rid: ("UI_NOT_REQUIRED" if _link(decision, rid).get("ui_scope") == "NO_UI" else "UI_SCOPE_PLANNED") for rid in requirement_ids}
                return ThinFrontendDesignResult(decision, states)
            feedback = [issue.format() for issue in issues]
        if not issues:
            issues = [_issue(FrontendDesignErrorCode.UI_SCOPE_MODEL_FAILED, feedback[-1] if feedback else "Frontend model failed.")]
        return ThinFrontendDesignResult({}, {rid: "FAILED" for rid in requirement_ids}, [row.format() for row in issues])


def _context_audit(
    *,
    phase: str,
    attempt: int,
    request_payload: dict[str, Any],
) -> str:
    def size(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    input_payload = request_payload.get("input_payload", {})
    section_sizes = sorted(
        ((str(key), size(value)) for key, value in input_payload.items()),
        key=lambda item: item[1],
        reverse=True,
    ) if isinstance(input_payload, dict) else []
    return (
        f"CONTEXT_AUDIT phase={phase} attempt={attempt} "
        f"context_total_chars={size(request_payload)} "
        f"instructions_chars={size(request_payload.get('instructions', ''))} "
        f"input_payload_chars={size(input_payload)} "
        f"output_schema_chars={size(request_payload.get('output_schema', {}))} "
        f"section_chars="
        + ",".join(f"{key}:{value}" for key, value in section_sizes)
    )


def validate_thin_frontend_design(frontend_ir: dict[str, Any], *, expected_requirement_ids: set[str] | None = None, backend_api_ids: set[str] | None = None) -> list[FrontendDesignIssue]:
    errors = shape_errors(frontend_ir)
    if errors:
        return [_issue(FrontendDesignErrorCode.IR_INVALID, message) for message in errors]
    issues: list[FrontendDesignIssue] = []
    for table in ("screens", "journeys", "shared_state_policies"):
        ids = [str(row["id"]) for row in frontend_ir[table]]
        if len(ids) != len(set(ids)):
            issues.append(_issue(FrontendDesignErrorCode.SYMBOL_DUPLICATE, f"{table} contains duplicate ids."))
    screens = {str(row["id"]): row for row in frontend_ir["screens"]}
    routes: dict[str, str] = {}
    for screen_id, screen in screens.items():
        route = str(screen["route"])
        if route in routes:
            issues.append(_issue(FrontendDesignErrorCode.ROUTE_CONFLICT, f"Route {route!r} is shared by {routes[route]} and {screen_id}."))
        routes[route] = screen_id
    states = {str(row["id"]) for row in frontend_ir["shared_state_policies"]}
    visuals = {str(row["id"]) for row in frontend_ir["visual_references"]}
    apis = backend_api_ids
    for screen_id, screen in screens.items():
        if apis is not None:
            issues.extend(_unknown(screen_id, "API", screen["required_api_ids"], apis, FrontendDesignErrorCode.API_DEPENDENCY_INVALID))
        issues.extend(_unknown(screen_id, "visual", screen["visual_reference_ids"], visuals, FrontendDesignErrorCode.REFERENCE_UNKNOWN))
        for navigation in screen["navigation_targets"]:
            target_route = str(navigation["target_route"])
            if target_route != "/" and target_route not in routes:
                issues.append(_issue(FrontendDesignErrorCode.REFERENCE_UNKNOWN, f"Screen {screen_id} navigates to unknown route {target_route}."))
    for journey in frontend_ir["journeys"]:
        if journey["source_screen_id"] not in screens:
            issues.append(_issue(FrontendDesignErrorCode.REFERENCE_UNKNOWN, f"Journey {journey['id']} references unknown screen {journey['source_screen_id']}."))
        if apis is not None and journey["api_id"] is not None and journey["api_id"] not in apis:
            issues.append(_issue(FrontendDesignErrorCode.API_DEPENDENCY_INVALID, f"Journey {journey['id']} references unknown API {journey['api_id']}."))
        success_route = journey["success_target_route"]
        if success_route is not None and success_route != "/" and success_route not in routes:
            issues.append(_issue(FrontendDesignErrorCode.REFERENCE_UNKNOWN, f"Journey {journey['id']} references unknown success route {success_route}."))
    for usage in frontend_ir["api_usages"]:
        if usage["screen_id"] not in screens or (apis is not None and usage["api_id"] not in apis):
            issues.append(_issue(FrontendDesignErrorCode.API_DEPENDENCY_INVALID, f"Invalid API usage {usage['screen_id']} -> {usage['api_id']}."))
    usage_pairs = {(str(row["screen_id"]), str(row["api_id"])) for row in frontend_ir["api_usages"]}
    for screen_id, screen in screens.items():
        missing = [api_id for api_id in screen["required_api_ids"] if (screen_id, str(api_id)) not in usage_pairs]
        if missing:
            issues.append(_issue(FrontendDesignErrorCode.API_DEPENDENCY_INVALID, f"Screen {screen_id} has APIs without api_usages: {missing}."))
    links = {str(row["requirement_id"]): row for row in frontend_ir["requirement_links"]}
    if len(links) != len(frontend_ir["requirement_links"]):
        issues.append(_issue(FrontendDesignErrorCode.SYMBOL_DUPLICATE, "requirement_links contains duplicate requirement ids."))
    if expected_requirement_ids is not None and set(links) != expected_requirement_ids:
        issues.append(_issue(FrontendDesignErrorCode.REQUIREMENT_UNCOVERED, f"Requirement links mismatch: missing={sorted(expected_requirement_ids - set(links))} extra={sorted(set(links) - expected_requirement_ids)}."))
    for rid, link in links.items():
        issues.extend(_unknown(rid, "screen", link["screen_ids"], set(screens), FrontendDesignErrorCode.REFERENCE_UNKNOWN))
        issues.extend(_unknown(rid, "shared state", link["shared_state_ids"], states, FrontendDesignErrorCode.REFERENCE_UNKNOWN))
        issues.extend(_unknown(rid, "visual", link["visual_reference_ids"], visuals, FrontendDesignErrorCode.REFERENCE_UNKNOWN))
        if link["ui_scope"] == "UI_REQUIRED" and not link["screen_ids"]:
            issues.append(_issue(FrontendDesignErrorCode.REQUIREMENT_UNCOVERED, f"UI_REQUIRED requirement {rid} has no screen."))
    return issues


def project_frontend_runtime_ir(frontend_ir: dict[str, Any]) -> dict[str, Any]:
    """Adapt thin design facts to existing runtime seams without persisting component design."""
    state_ids = [str(row["id"]) for row in frontend_ir.get("shared_state_policies", [])]
    pages = []
    for screen in frontend_ir.get("screens", []):
        pages.append({
            "id": screen["id"], "spec": screen["purpose"], "route": screen["route"], "route_inputs": copy.deepcopy(screen["route_inputs"]),
            "requirement_ids": copy.deepcopy(screen["requirement_ids"]), "layout_id": None, "component_ids": [],
            "api_dependencies": copy.deepcopy(screen["required_api_ids"]),
            "store_dependencies": [sid for sid in state_ids if _state_used(frontend_ir, sid, screen["requirement_ids"])],
            "render_obligations": [{"id": f"state_{index}", "kind": "REGION", "label": value, "semantic_id": None, "required": True} for index, value in enumerate(screen["observable_states"][:12], 1)],
            "navigation": [{"trigger": row["trigger"], "target": row["target_route"], "target_route": row["target_route"], "condition": row["condition"]} for row in screen["navigation_targets"]],
            "visual_reference_ids": copy.deepcopy(screen["visual_reference_ids"]),
        })
    stores = [{"id": row["id"], "spec": row["purpose"], "state": copy.deepcopy(row["state"]), "actions": copy.deepcopy(row["actions"]), "persistence": copy.deepcopy(row["persistence"]), "requirement_ids": copy.deepcopy(row["requirement_ids"])} for row in frontend_ir.get("shared_state_policies", [])]
    dependencies = [{"consumer_id": row["screen_id"], "api_id": row["api_id"], "bindings": copy.deepcopy(row["request_bindings"] + row["response_bindings"])} for row in frontend_ir.get("api_usages", [])]
    links = [{"requirement_id": row["requirement_id"], "ui_scope": row["ui_scope"], "symbol_ids": sorted(set(row["screen_ids"] + row["shared_state_ids"])), "visual_reference_ids": copy.deepcopy(row["visual_reference_ids"])} for row in frontend_ir.get("requirement_links", [])]
    return {"schema_version": 2, "visual_references": copy.deepcopy(frontend_ir.get("visual_references", [])), "layouts": [], "pages": pages, "components": [], "stores": stores, "api_dependencies": dependencies, "requirement_links": links}


def frontend_design_traceability(frontend_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["requirement_id"]): {"ui_scope": row["ui_scope"], "layout_ids": [], "component_ids": [], "page_ids": copy.deepcopy(row["screen_ids"]), "store_ids": copy.deepcopy(row["shared_state_ids"]), "visual_reference_ids": copy.deepcopy(row["visual_reference_ids"])} for row in frontend_ir.get("requirement_links", [])}


def _canonicalize(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    for table in ("screens", "journeys", "shared_state_policies"):
        result[table] = sorted(result[table], key=lambda row: str(row["id"]))
    result["api_usages"] = sorted(result["api_usages"], key=lambda row: (row["screen_id"], row["api_id"]))
    result["requirement_links"] = sorted(result["requirement_links"], key=lambda row: row["requirement_id"])
    return result


def _link(frontend_ir: dict[str, Any], rid: str) -> dict[str, Any]:
    return next((row for row in frontend_ir["requirement_links"] if row["requirement_id"] == rid), {})


def _state_used(frontend_ir: dict[str, Any], state_id: str, requirement_ids: list[str]) -> bool:
    owners = set(requirement_ids)
    return any(state_id in row["shared_state_ids"] and row["requirement_id"] in owners for row in frontend_ir.get("requirement_links", []))


def _unknown(owner: str, kind: str, values: list[Any], allowed: set[str], code: FrontendDesignErrorCode) -> list[FrontendDesignIssue]:
    return [_issue(code, f"{owner} references unknown {kind} {value}.") for value in values if str(value) not in allowed]


def _issue(code: FrontendDesignErrorCode, message: str) -> FrontendDesignIssue:
    return FrontendDesignIssue(code, message, "THIN_FRONTEND_DESIGN", "FRONTEND")


def _provider_schema(schema: dict[str, Any]) -> dict[str, Any]:
    unsupported = {"maxLength", "uniqueItems"}
    return {key: _provider_schema(value) if isinstance(value, dict) else [_provider_schema(row) if isinstance(row, dict) else row for row in value] if isinstance(value, list) else value for key, value in schema.items() if key not in unsupported}
