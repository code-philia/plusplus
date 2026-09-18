"""Component decomposition, Frontend traceability, and API binding finalization."""

from __future__ import annotations

import copy
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from core.logging import SynchronousLog

from .frontend_design import (
    FrontendDesignState,
    validate_frontend_design_minimum,
)
from .frontend_ir import (
    EVENT_SCHEMA,
    RENDER_OBLIGATION_SCHEMA,
    SEMANTIC_FIELD_SCHEMA,
    FrontendDesignErrorCode,
    FrontendDesignIssue,
    repair_schema_shape,
    schema_shape_errors,
)
from .model_client import StructuredModel, describe_model_error


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _string_list(*, max_items: int = 64, max_length: int = 240) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": max_items,
        "items": {"type": "string", "minLength": 1, "maxLength": max_length},
    }


COMPONENT_PLAN_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action",
        "name",
        "scope",
        "spec",
        "inputs",
        "events",
        "render_obligations",
        "visual_reference_ids",
    ],
    "properties": {
        "action": {"type": "string", "enum": ["CREATE", "REUSE"]},
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "scope": {"type": "string", "enum": ["LAYOUT", "PAGE", "SHARED"]},
        "spec": {"type": "string", "maxLength": 800},
        "inputs": {"type": "array", "items": SEMANTIC_FIELD_SCHEMA},
        "events": {"type": "array", "items": EVENT_SCHEMA},
        "render_obligations": {"type": "array", "items": RENDER_OBLIGATION_SCHEMA},
        "visual_reference_ids": _string_list(),
    },
}

PAGE_LAYOUT_COMPONENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["parent_id", "components"],
    "properties": {
        "parent_id": {"type": "string", "minLength": 1},
        "components": {
            "type": "array",
            "maxItems": 24,
            "items": COMPONENT_PLAN_ITEM_SCHEMA,
        },
    },
}

COMPONENT_INSTRUCTIONS = """Decompose exactly one Page or Layout into its direct meaningful functional Components.
Return only the supplied JSON shape. Do not recursively create a DOM tree or implementation details. CREATE defines
one new global component contract; REUSE must reference an existing compatible component by copying its registry name
without the COMPONENT prefix. A Page parent may create PAGE or SHARED components. A Layout parent may create LAYOUT
or SHARED components. Use SHARED only when the same functional contract is genuinely reusable by multiple parents.
For REUSE, keep create-only fields as an empty string or empty arrays; the compiler ignores them. Inputs are external
component props expressed with stable semantic ids. Events are callbacks emitted to the parent. Render obligations
record observable fields, actions, regions, navigation, text, or feedback, not JSX/CSS. Use only supplied visual ids.
When an input reuses a semantic_id from a requirement or API contract, copy its canonical type exactly. Do not add
nullability, undefined, optionality, GUESS markers, or other qualifiers unless that exact type is present in the
contract. `required: false` expresses an optional prop; it does not change the field type. Never emit placeholder
types such as `string|null.GUESS?`.
Do not create Stores, routes, API clients, files, nested child components, CSS, or implementation logic. Return only
the structured object required by the supplied schema.
"""


@dataclass(slots=True)
class FrontendDesignPassResult:
    frontend_ir: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class PageLayoutComponentPass:
    """Decompose every global Layout and Page into direct Components."""

    def __init__(self, model: StructuredModel, artifact_root: Path) -> None:
        self._model = model
        self._retry_count = _bounded_env_int("ARC_STRUCTURED_OUTPUT_RETRY_COUNT", 2, 0, 10)
        self._trace_enabled = _env_flag("ARC_FRONTEND_DESIGN_TRACE", True)
        self._log = _frontend_log("PageLayoutComponentPass", artifact_root)

    def compile(
        self,
        frontend_ir: dict[str, Any],
        backend_design_ir: dict[str, Any],
    ) -> FrontendDesignPassResult:
        state = FrontendDesignState.from_ir(frontend_ir)
        backend_apis = _backend_apis(backend_design_ir)
        contracts = _requirement_contracts(backend_design_ir)
        all_issues: list[FrontendDesignIssue] = []
        parent_ids = [*sorted(state.layouts), *sorted(state.pages)]

        for parent_id in parent_ids:
            parent = state.layouts.get(parent_id) or state.pages[parent_id]
            requirement_ids = _parent_requirement_ids(state, parent_id)
            api_ids = _parent_api_ids(state, parent_id)
            allowed_visual_ids = {str(value) for value in parent["visual_reference_ids"]}
            decision, issues = self._decide(
                parent_id=parent_id,
                parent=parent,
                requirement_contracts=[
                    copy.deepcopy(contracts[value])
                    for value in requirement_ids
                    if value in contracts
                ],
                backend_apis=[
                    _model_api(backend_apis[value])
                    for value in api_ids
                    if value in backend_apis
                ],
                visual_references=[
                    copy.deepcopy(state.visual_references[value])
                    for value in sorted(allowed_visual_ids)
                    if value in state.visual_references
                ],
                existing_components=[
                    _model_component(state.components[value])
                    for value in sorted(state.components)
                ],
                state=state,
                allowed_visual_ids=allowed_visual_ids,
            )
            if decision is None:
                all_issues.extend(issues)
                break
            apply_issues = _apply_component_decision(
                state,
                parent_id,
                decision,
                allowed_visual_ids=allowed_visual_ids,
            )
            if apply_issues:
                all_issues.extend(apply_issues)
                break

        result_ir = state.to_ir()
        if not all_issues:
            all_issues.extend(validate_frontend_design_minimum(
                result_ir,
                expected_requirement_ids=set(state.requirement_links),
                backend_api_ids=set(backend_apis),
            ))
        return _result(result_ir, all_issues)

    def _decide(
        self,
        *,
        parent_id: str,
        parent: dict[str, Any],
        requirement_contracts: list[dict[str, Any]],
        backend_apis: list[dict[str, Any]],
        visual_references: list[dict[str, Any]],
        existing_components: list[dict[str, Any]],
        state: FrontendDesignState,
        allowed_visual_ids: set[str],
    ) -> tuple[dict[str, Any] | None, list[FrontendDesignIssue]]:
        feedback: list[str] = []
        for attempt in range(self._retry_count + 1):
            payload: dict[str, Any] = {
                "parent": copy.deepcopy(parent),
                "requirement_contracts": requirement_contracts,
                "backend_apis": backend_apis,
                "visual_references": [_model_visual(value) for value in visual_references],
                "existing_component_registry": existing_components,
            }
            if feedback:
                payload["validation_feedback"] = feedback
            self._trace(
                f"MODEL_REQUEST phase=page_layout_component unit={parent_id} "
                f"attempt={attempt + 1}/{self._retry_count + 1}"
            )
            provider_schema = _provider_output_schema(PAGE_LAYOUT_COMPONENT_SCHEMA)
            self._trace_json(
                "MODEL_INPUT",
                "page_layout_component",
                parent_id,
                {
                    "schema_name": "arc_page_layout_components",
                    "instructions": COMPONENT_INSTRUCTIONS,
                    "input_payload": payload,
                    "output_schema": provider_schema,
                    "local_validation_schema": PAGE_LAYOUT_COMPONENT_SCHEMA,
                },
            )
            started = time.perf_counter()
            try:
                raw_decision = self._model.generate_json(
                    schema_name="arc_page_layout_components",
                    instructions=COMPONENT_INSTRUCTIONS,
                    input_payload=payload,
                    output_schema=provider_schema,
                )
            except Exception as exc:
                detail = describe_model_error(exc)
                issue = _issue(
                    FrontendDesignErrorCode.COMPONENT_MODEL_FAILED,
                    f"Component model call failed: {detail}",
                    "PAGE_LAYOUT_COMPONENT",
                    parent_id,
                )
                feedback = [issue.format()]
                self._trace(f"MODEL_ERROR phase=page_layout_component unit={parent_id} error={detail}")
                continue
            duration = int((time.perf_counter() - started) * 1000)
            self._trace_json(
                "MODEL_OUTPUT",
                "page_layout_component",
                parent_id,
                raw_decision,
                duration,
            )
            decision = _repair_component_decision(
                state,
                parent_id,
                raw_decision,
                allowed_visual_ids=allowed_visual_ids,
            )
            if decision != raw_decision:
                self._trace(
                    f"MODEL_REPAIRED phase=page_layout_component unit={parent_id} "
                    f"attempt={attempt + 1}"
                )
                self._trace_json(
                    "MODEL_REPAIRED_OUTPUT",
                    "page_layout_component",
                    parent_id,
                    decision,
                )
            issues = _component_decision_issues(
                state,
                parent_id,
                decision,
                allowed_visual_ids=allowed_visual_ids,
                canonical_types=_canonical_types(requirement_contracts, backend_apis),
            )
            if not issues:
                self._trace(
                    f"MODEL_ACCEPTED phase=page_layout_component unit={parent_id} "
                    f"attempt={attempt + 1} duration_ms={duration}"
                )
                return decision, []
            feedback = [issue.format() for issue in issues]
            self._trace(
                f"MODEL_REJECTED phase=page_layout_component unit={parent_id} "
                f"errors={'; '.join(feedback)}"
            )
        self._trace(
            f"MODEL_FALLBACK phase=page_layout_component unit={parent_id} "
            "decision=empty_component_plan"
        )
        return {"parent_id": parent_id, "components": []}, []

    def _trace(self, message: str) -> None:
        if self._trace_enabled:
            self._log.info(message)

    def _trace_json(
        self,
        marker: str,
        phase: str,
        unit_id: str,
        payload: Any,
        duration: int | None = None,
    ) -> None:
        suffix = f" duration_ms={duration}" if duration is not None else ""
        self._trace(
            f"{marker} phase={phase} unit={unit_id}{suffix}\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)}"
        )


def frontend_design_traceability(
    frontend_ir: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Project compact Requirement-to-Frontend-Design links."""

    symbol_kind = {
        str(item["id"]): key
        for key in ("layouts", "pages", "components", "stores")
        for item in frontend_ir.get(key, [])
        if isinstance(item, dict) and item.get("id")
    }
    result: dict[str, dict[str, Any]] = {}
    for link in frontend_ir.get("requirement_links", []):
        if not isinstance(link, dict):
            continue
        requirement_id = str(link.get("requirement_id", ""))
        row = {
            "ui_scope": str(link.get("ui_scope", "NO_UI")),
            "layout_ids": [],
            "page_ids": [],
            "component_ids": [],
            "store_ids": [],
            "visual_reference_ids": _sorted_unique(link.get("visual_reference_ids", [])),
        }
        key_by_table = {
            "layouts": "layout_ids",
            "pages": "page_ids",
            "components": "component_ids",
            "stores": "store_ids",
        }
        for symbol_id in link.get("symbol_ids", []):
            table = symbol_kind.get(str(symbol_id))
            if table:
                row[key_by_table[table]].append(str(symbol_id))
        result[requirement_id] = {
            key: (_sorted_unique(values) if isinstance(values, list) else values)
            for key, values in row.items()
        }
    return result


def finalize_frontend_design(
    frontend_ir: dict[str, Any],
    backend_design_ir: dict[str, Any],
) -> FrontendDesignPassResult:
    """Canonicalize Frontend Design and materialize best-effort API bindings."""

    state = FrontendDesignState.from_ir(frontend_ir)
    backend_apis = _backend_apis(backend_design_ir)

    for layout in state.layouts.values():
        layout["requirement_ids"] = _sorted_unique(layout["requirement_ids"])
        layout["component_ids"] = _ordered_unique(layout["component_ids"])
        layout["visual_reference_ids"] = _sorted_unique(layout["visual_reference_ids"])
    for store in state.stores.values():
        store["requirement_ids"] = _sorted_unique(store["requirement_ids"])
    for page in state.pages.values():
        page["requirement_ids"] = _sorted_unique(page["requirement_ids"])
        page["component_ids"] = _ordered_unique(page["component_ids"])
        page["api_dependencies"] = _sorted_unique(page["api_dependencies"])
        page["store_dependencies"] = _sorted_unique(page["store_dependencies"])
        page["visual_reference_ids"] = _sorted_unique(page["visual_reference_ids"])
    for link in state.requirement_links.values():
        link["symbol_ids"] = _sorted_unique(link["symbol_ids"])
        link["visual_reference_ids"] = _sorted_unique(link["visual_reference_ids"])

    issues = _materialize_unassigned_page_apis(state, backend_apis)
    finalized = state.to_ir()
    issues.extend(validate_frontend_design_minimum(
        finalized,
        expected_requirement_ids=set(state.requirement_links),
        backend_api_ids=set(backend_apis),
    ))
    return _result(finalized, issues)


def _repair_component_decision(
    state: FrontendDesignState,
    parent_id: str,
    decision: Any,
    *,
    allowed_visual_ids: set[str],
) -> dict[str, Any]:
    shaped = (
        repair_schema_shape(decision, PAGE_LAYOUT_COMPONENT_SCHEMA)
        if isinstance(decision, dict)
        else {}
    )
    raw_components = shaped.get("components") if isinstance(shaped, dict) else []
    if not isinstance(raw_components, list):
        raw_components = []
    parent = state.pages.get(parent_id) or state.layouts[parent_id]
    default_scope = "PAGE" if parent_id in state.pages else "LAYOUT"
    allowed_scopes = {default_scope, "SHARED"}
    default_spec = str(parent.get("spec", "")).strip()[:800] or f"UI for {parent_id}."
    components: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for index, raw in enumerate(raw_components[:24]):
        if not isinstance(raw, dict):
            continue
        name = _tolerant_name(raw.get("name"), f"{_id_suffix(parent_id)}Component{index + 1}")
        component_id = state.stable_symbol_id("COMPONENT", name)
        if component_id in seen_ids:
            continue
        existing = state.components.get(component_id)
        if existing is not None and not _component_can_attach(existing, parent_id):
            continue
        seen_ids.add(component_id)
        create = existing is None
        scope = str(raw.get("scope", "")).upper()
        if scope not in allowed_scopes:
            scope = default_scope
        components.append({
            "action": "CREATE" if create else "REUSE",
            "name": name,
            "scope": scope,
            "spec": "" if not create else _bounded_string(raw.get("spec"), 800, default_spec),
            "inputs": _unique_rows(
                _repair_rows(raw.get("inputs"), SEMANTIC_FIELD_SCHEMA),
                "semantic_id",
            ),
            "events": _unique_rows(
                _repair_rows(raw.get("events"), EVENT_SCHEMA),
                "name",
            ),
            "render_obligations": _unique_rows(
                _repair_rows(raw.get("render_obligations"), RENDER_OBLIGATION_SCHEMA),
                "id",
            ),
            "visual_reference_ids": _allowed_values(
                raw.get("visual_reference_ids"), allowed_visual_ids
            ),
        })
    return {"parent_id": parent_id, "components": components}


def _repair_rows(values: Any, schema: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    rows: list[dict[str, Any]] = []
    for value in values[:64]:
        repaired = repair_schema_shape(value, schema)
        if isinstance(repaired, dict) and not schema_shape_errors(repaired, schema):
            rows.append(repaired)
    return rows


def _unique_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        value = str(row.get(key, ""))
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(row)
    return result


def _allowed_values(values: Any, allowed: set[str]) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({str(value) for value in values if str(value) in allowed})


def _bounded_string(value: Any, maximum: int, fallback: str) -> str:
    text = str(value).strip() if value is not None else ""
    return (text or fallback)[:maximum]


def _id_suffix(symbol_id: str) -> str:
    return symbol_id.split(".", 1)[-1] if "." in symbol_id else symbol_id


def _tolerant_name(value: Any, fallback: str) -> str:
    raw = str(value).strip() if value is not None else ""
    if "." in raw and raw.split(".", 1)[0].upper() in {
        "PAGE", "LAYOUT", "COMPONENT", "STORE"
    }:
        raw = raw.split(".", 1)[1]
    parts = re.findall(r"[A-Za-z0-9]+", raw)
    name = "".join(part[:1].upper() + part[1:] for part in parts) or fallback
    if name[0].isdigit():
        name = f"Ui{name}"
    return name[:80]


def _component_decision_issues(
    state: FrontendDesignState,
    parent_id: str,
    decision: Any,
    *,
    allowed_visual_ids: set[str],
    canonical_types: dict[str, str] | None = None,
) -> list[FrontendDesignIssue]:
    shape_errors = schema_shape_errors(decision, PAGE_LAYOUT_COMPONENT_SCHEMA)
    if shape_errors:
        return [_issue(
            FrontendDesignErrorCode.COMPONENT_DECISION_INVALID,
            value,
            "PAGE_LAYOUT_COMPONENT",
            parent_id,
        ) for value in shape_errors]
    assert isinstance(decision, dict)
    issues: list[FrontendDesignIssue] = []
    if decision["parent_id"] != parent_id:
        issues.append(_issue(
            FrontendDesignErrorCode.COMPONENT_DECISION_INVALID,
            f"Component decision parent_id must be {parent_id!r}.",
            "PAGE_LAYOUT_COMPONENT",
            parent_id,
        ))
    is_page = parent_id in state.pages
    allowed_scopes = {"PAGE", "SHARED"} if is_page else {"LAYOUT", "SHARED"}
    seen_ids: set[str] = set()
    for item in decision["components"]:
        try:
            component_id = state.stable_symbol_id("COMPONENT", item["name"])
        except ValueError as exc:
            issues.append(_issue(
                FrontendDesignErrorCode.COMPONENT_DECISION_INVALID,
                str(exc),
                "PAGE_LAYOUT_COMPONENT",
                parent_id,
            ))
            continue
        if component_id in seen_ids:
            issues.append(_issue(
                FrontendDesignErrorCode.COMPONENT_DECISION_INVALID,
                f"Decision mentions {component_id} more than once.",
                "PAGE_LAYOUT_COMPONENT",
                component_id,
            ))
        seen_ids.add(component_id)
        if item["action"] == "CREATE":
            if component_id in state.components:
                issues.append(_issue(
                    FrontendDesignErrorCode.COMPONENT_CREATION_CONFLICT,
                    f"CREATE conflicts with existing component {component_id}; use REUSE.",
                    "PAGE_LAYOUT_COMPONENT",
                    component_id,
                ))
            if item["scope"] not in allowed_scopes:
                issues.append(_issue(
                    FrontendDesignErrorCode.COMPONENT_DECISION_INVALID,
                    f"{parent_id} cannot create a {item['scope']} component.",
                    "PAGE_LAYOUT_COMPONENT",
                    component_id,
                ))
            if not str(item["spec"]).strip():
                issues.append(_issue(
                    FrontendDesignErrorCode.COMPONENT_DECISION_INVALID,
                    f"CREATE {component_id} requires a non-empty spec.",
                    "PAGE_LAYOUT_COMPONENT",
                    component_id,
                ))
            invalid_visuals = sorted(set(item["visual_reference_ids"]) - allowed_visual_ids)
            if invalid_visuals:
                issues.append(_issue(
                    FrontendDesignErrorCode.REFERENCE_UNKNOWN,
                    f"Component {component_id} uses unavailable visual references: {invalid_visuals}.",
                    "PAGE_LAYOUT_COMPONENT",
                    component_id,
                ))
            issues.extend(_semantic_field_issues(
                item["inputs"],
                component_id,
                code=FrontendDesignErrorCode.COMPONENT_DECISION_INVALID,
                phase="PAGE_LAYOUT_COMPONENT",
            ))
            issues.extend(_component_field_type_issues(
                state,
                component_id,
                item["inputs"],
                canonical_types or {},
            ))
            event_names = [str(value["name"]) for value in item["events"]]
            if len(event_names) != len(set(event_names)):
                issues.append(_issue(
                    FrontendDesignErrorCode.COMPONENT_DECISION_INVALID,
                    f"CREATE {component_id} repeats event names.",
                    "PAGE_LAYOUT_COMPONENT",
                    component_id,
                ))
            obligation_ids = [str(value["id"]) for value in item["render_obligations"]]
            if len(obligation_ids) != len(set(obligation_ids)):
                issues.append(_issue(
                    FrontendDesignErrorCode.COMPONENT_DECISION_INVALID,
                    f"CREATE {component_id} repeats render obligation ids.",
                    "PAGE_LAYOUT_COMPONENT",
                    component_id,
                ))
        else:
            existing = state.components.get(component_id)
            if existing is None:
                issues.append(_issue(
                    FrontendDesignErrorCode.COMPONENT_REUSE_MISSING,
                    f"REUSE references missing component {component_id}.",
                    "PAGE_LAYOUT_COMPONENT",
                    component_id,
                ))
            elif not _component_can_attach(existing, parent_id):
                issues.append(_issue(
                    FrontendDesignErrorCode.COMPONENT_DECISION_INVALID,
                    f"Component {component_id} is owned by another parent and is not SHARED.",
                    "PAGE_LAYOUT_COMPONENT",
                    component_id,
                ))
    return issues


def _canonical_types(
    requirement_contracts: Iterable[dict[str, Any]],
    backend_apis: Iterable[dict[str, Any]],
) -> dict[str, str]:
    """Build the authoritative semantic-id -> type map visible to a parent."""
    result: dict[str, str] = {}
    for owner in [*requirement_contracts, *backend_apis]:
        if not isinstance(owner, dict):
            continue
        for field in [*owner.get("inputs", []), *owner.get("outputs", [])]:
            if not isinstance(field, dict):
                continue
            semantic_id = str(field.get("semantic_id", "")).strip()
            field_type = str(field.get("type", "")).strip()
            if semantic_id and field_type:
                result.setdefault(semantic_id, field_type)
    return result


def _component_field_type_issues(
    state: FrontendDesignState,
    component_id: str,
    fields: Iterable[dict[str, Any]],
    canonical_types: dict[str, str],
) -> list[FrontendDesignIssue]:
    issues: list[FrontendDesignIssue] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        semantic_id = str(field.get("semantic_id", "")).strip()
        field_type = str(field.get("type", "")).strip()
        if not semantic_id or not field_type:
            continue
        if re.search(r"(?:GUESS|TODO|UNKNOWN|PLACEHOLDER|\?)", field_type, re.IGNORECASE):
            issues.append(_issue(
                FrontendDesignErrorCode.BINDING_INCOMPATIBLE,
                f"{component_id} uses a non-canonical placeholder type {field_type!r} for {semantic_id}.",
                "FRONTEND_SEMANTIC_VALIDATION",
                component_id,
            ))
        expected = canonical_types.get(semantic_id)
        if expected and field_type != expected:
            issues.append(_issue(
                FrontendDesignErrorCode.BINDING_INCOMPATIBLE,
                f"{component_id} defines {semantic_id} as {field_type!r}; canonical type is {expected!r}.",
                "FRONTEND_SEMANTIC_VALIDATION",
                component_id,
            ))
        for existing in state.components.values():
            for prior in existing.get("inputs", []):
                if (
                    isinstance(prior, dict)
                    and prior.get("semantic_id") == semantic_id
                    and prior.get("type") != field_type
                ):
                    issues.append(_issue(
                        FrontendDesignErrorCode.BINDING_INCOMPATIBLE,
                        f"{component_id} conflicts with existing component field {semantic_id}: "
                        f"{field_type!r} vs {prior.get('type')!r}.",
                        "FRONTEND_SEMANTIC_VALIDATION",
                        component_id,
                    ))
    return issues


def _apply_component_decision(
    state: FrontendDesignState,
    parent_id: str,
    decision: dict[str, Any],
    *,
    allowed_visual_ids: set[str],
) -> list[FrontendDesignIssue]:
    issues = _component_decision_issues(
        state,
        parent_id,
        decision,
        allowed_visual_ids=allowed_visual_ids,
    )
    if issues:
        return issues
    trial = state.clone()
    parent = trial.pages.get(parent_id) or trial.layouts[parent_id]
    requirement_ids = _parent_requirement_ids(trial, parent_id)
    for item in decision["components"]:
        component_id = trial.stable_symbol_id("COMPONENT", item["name"])
        if item["action"] == "CREATE":
            scope = item["scope"]
            trial.components[component_id] = {
                "id": component_id,
                "spec": str(item["spec"]).strip(),
                "scope": scope,
                "owner_page_id": parent_id if scope == "PAGE" else None,
                "owner_layout_id": parent_id if scope == "LAYOUT" else None,
                "inputs": copy.deepcopy(item["inputs"]),
                "events": copy.deepcopy(item["events"]),
                "render_obligations": copy.deepcopy(item["render_obligations"]),
                "visual_reference_ids": _sorted_unique(item["visual_reference_ids"]),
            }
        if component_id not in parent["component_ids"]:
            parent["component_ids"].append(component_id)
        for requirement_id in requirement_ids:
            _add_sorted_unique(trial.requirement_links[requirement_id]["symbol_ids"], component_id)
    _replace_state(state, trial)
    return []


def _materialize_unassigned_page_apis(
    state: FrontendDesignState,
    backend_apis: dict[str, dict[str, Any]],
) -> list[FrontendDesignIssue]:
    issues: list[FrontendDesignIssue] = []
    for page_id, page in sorted(state.pages.items()):
        covered = {
            str(value["api_id"])
            for value in state.api_dependencies
            if value["consumer_id"] == page_id
            or (
                value["consumer_id"] in state.components
                and page_id in _component_page_ids(state, str(value["consumer_id"]))
            )
        }
        for api_id in page["api_dependencies"]:
            if api_id in covered:
                continue
            api = backend_apis.get(api_id)
            if api is None:
                issues.append(_issue(
                    FrontendDesignErrorCode.API_DEPENDENCY_INVALID,
                    f"Page {page_id} references missing Backend API {api_id}.",
                    "FRONTEND_CONTRACT_BINDING",
                    page_id,
                ))
                continue
            sources = _page_source_fields(state, page_id)
            bindings = _infer_bindings([], sources, api.get("inputs", []))
            _, source_issues = _field_catalog(sources, page_id, "source")
            _, target_issues = _field_catalog(api.get("inputs", []), api_id, "target")
            issues.extend(source_issues)
            issues.extend(target_issues)
            if source_issues or target_issues:
                continue
            state.api_dependencies.append({
                "consumer_id": page_id,
                "api_id": api_id,
                "bindings": bindings,
            })
    return issues


def _infer_bindings(
    explicit: Iterable[dict[str, Any]],
    source_fields: Iterable[dict[str, Any]],
    target_fields: Iterable[dict[str, Any]],
) -> list[dict[str, str]]:
    result = [
        {
            "source_semantic_id": str(value["source_semantic_id"]),
            "target_semantic_id": str(value["target_semantic_id"]),
        }
        for value in explicit
    ]
    bound_targets = {value["target_semantic_id"] for value in result}
    sources = [value for value in source_fields if isinstance(value, dict)]
    for target in target_fields:
        if not isinstance(target, dict):
            continue
        target_id = str(target.get("semantic_id", ""))
        if not target_id or target_id in bound_targets:
            continue
        exact = [
            value for value in sources
            if value.get("semantic_id") == target_id and value.get("type") == target.get("type")
        ]
        candidates = exact or [
            value for value in sources
            if value.get("name") == target.get("name") and value.get("type") == target.get("type")
        ]
        if len(candidates) == 1:
            result.append({
                "source_semantic_id": str(candidates[0]["semantic_id"]),
                "target_semantic_id": target_id,
            })
            bound_targets.add(target_id)
    return sorted(result, key=lambda value: (value["target_semantic_id"], value["source_semantic_id"]))


def _component_source_fields(
    state: FrontendDesignState,
    component_id: str,
) -> list[dict[str, Any]]:
    return _canonical_fields(copy.deepcopy(state.components[component_id]["inputs"]))


def _page_source_fields(
    state: FrontendDesignState,
    page_id: str,
) -> list[dict[str, Any]]:
    page = state.pages[page_id]
    result = copy.deepcopy(page["route_inputs"])
    for store_id in page["store_dependencies"]:
        result.extend(copy.deepcopy(state.stores[store_id]["state"]))
    for component_id in _page_component_ids(state, page_id):
        result.extend(_component_source_fields(state, component_id))
    return _canonical_fields(result)


def _canonical_fields(fields: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[tuple[str, str, str, bool], dict[str, Any]] = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        semantic_id = str(field.get("semantic_id", ""))
        signature = (
            semantic_id,
            str(field.get("name", "")),
            str(field.get("type", "")),
            bool(field.get("required")),
        )
        if semantic_id:
            result.setdefault(signature, copy.deepcopy(field))
    return [result[value] for value in sorted(result)]


def _field_catalog(
    fields: Iterable[dict[str, Any]],
    owner_id: str,
    label: str,
) -> tuple[dict[str, dict[str, Any]], list[FrontendDesignIssue]]:
    catalog: dict[str, dict[str, Any]] = {}
    issues: list[FrontendDesignIssue] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        semantic_id = str(field.get("semantic_id", ""))
        existing = catalog.get(semantic_id)
        if existing is not None and (
            existing.get("type") != field.get("type")
            or existing.get("name") != field.get("name")
        ):
            issues.append(_issue(
                FrontendDesignErrorCode.BINDING_INCOMPATIBLE,
                f"{owner_id} has conflicting {label} field definitions for {semantic_id}: "
                f"{existing.get('type')!r}/{existing.get('name')!r} vs "
                f"{field.get('type')!r}/{field.get('name')!r}.",
                "FRONTEND_SEMANTIC_VALIDATION",
                owner_id,
            ))
        elif semantic_id:
            catalog[semantic_id] = field
    return catalog, issues


def _semantic_field_issues(
    fields: Iterable[dict[str, Any]],
    owner_id: str,
    *,
    code: FrontendDesignErrorCode,
    phase: str,
) -> list[FrontendDesignIssue]:
    _, issues = _field_catalog(fields, owner_id, "semantic")
    semantic_ids = [str(value.get("semantic_id", "")) for value in fields]
    if len(semantic_ids) != len(set(semantic_ids)):
        issues.append(_issue(
            code,
            f"{owner_id} repeats semantic field ids.",
            phase,
            owner_id,
        ))
    return issues


def _component_can_attach(component: dict[str, Any], parent_id: str) -> bool:
    return (
        component["scope"] == "SHARED"
        or component.get("owner_page_id") == parent_id
        or component.get("owner_layout_id") == parent_id
    )


def _parent_requirement_ids(state: FrontendDesignState, parent_id: str) -> list[str]:
    if parent_id in state.pages:
        return _sorted_unique(state.pages[parent_id]["requirement_ids"])
    layout = state.layouts[parent_id]
    values = list(layout["requirement_ids"])
    for page in state.pages.values():
        if page.get("layout_id") == parent_id:
            values.extend(page["requirement_ids"])
    return _sorted_unique(values)


def _parent_api_ids(state: FrontendDesignState, parent_id: str) -> list[str]:
    if parent_id in state.pages:
        return list(state.pages[parent_id]["api_dependencies"])
    values: list[str] = []
    for page in state.pages.values():
        if page.get("layout_id") == parent_id:
            values.extend(page["api_dependencies"])
    return _sorted_unique(values)


def _component_parent_ids(state: FrontendDesignState, component_id: str) -> list[str]:
    return sorted(
        parent_id
        for parent_id, parent in {**state.layouts, **state.pages}.items()
        if component_id in parent["component_ids"]
    )


def _component_page_ids(state: FrontendDesignState, component_id: str) -> list[str]:
    result: set[str] = set()
    for parent_id in _component_parent_ids(state, component_id):
        if parent_id in state.pages:
            result.add(parent_id)
        elif parent_id in state.layouts:
            result.update(
                page_id
                for page_id, page in state.pages.items()
                if page.get("layout_id") == parent_id
            )
    return sorted(result)


def _page_component_ids(state: FrontendDesignState, page_id: str) -> list[str]:
    result = set(str(value) for value in state.pages[page_id]["component_ids"])
    layout_id = state.pages[page_id].get("layout_id")
    if layout_id:
        result.update(str(value) for value in state.layouts[str(layout_id)]["component_ids"])
    return sorted(result)


def _backend_apis(backend_design_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(value["id"]): copy.deepcopy(value)
        for value in backend_design_ir.get("modules", [])
        if isinstance(value, dict) and value.get("kind") == "API" and value.get("id")
    }


def _requirement_contracts(backend_design_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(value["id"]): copy.deepcopy(value["contract"])
        for value in backend_design_ir.get("requirements", [])
        if isinstance(value, dict) and value.get("id") and isinstance(value.get("contract"), dict)
    }


def _model_api(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value.get(key, [] if key in {"inputs", "outputs"} else ""))
        for key in ("id", "spec", "inputs", "outputs")
    }


def _model_visual(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(value.get("id", "")),
        "source_path": str(value.get("source_path", "")),
        "analysis": copy.deepcopy(value.get("analysis", {})),
    }


def _model_component(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(value["id"]),
        "name": str(value["id"]).split(".", 1)[-1],
        "spec": str(value["spec"]),
        "scope": str(value["scope"]),
        "owner_page_id": value.get("owner_page_id"),
        "owner_layout_id": value.get("owner_layout_id"),
        "inputs": copy.deepcopy(value["inputs"]),
        "events": copy.deepcopy(value["events"]),
    }


def _replace_state(target: FrontendDesignState, source: FrontendDesignState) -> None:
    for name in (
        "visual_references",
        "layouts",
        "pages",
        "components",
        "stores",
        "api_dependencies",
        "requirement_links",
    ):
        setattr(target, name, getattr(source, name))


def _result(
    frontend_ir: dict[str, Any],
    issues: list[FrontendDesignIssue],
) -> FrontendDesignPassResult:
    return FrontendDesignPassResult(
        frontend_ir=frontend_ir,
        errors=[value.format() for value in issues],
    )


def _frontend_log(name: str, artifact_root: Path) -> SynchronousLog:
    arc_root = artifact_root.expanduser().resolve()
    return SynchronousLog(name, workspace_root=arc_root.parent)


def _provider_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    unsupported = {"maxLength", "uniqueItems"}
    return {
        key: _provider_output_schema(value) if isinstance(value, dict) else [
            _provider_output_schema(item) if isinstance(item, dict) else item
            for item in value
        ] if isinstance(value, list) else value
        for key, value in schema.items()
        if key not in unsupported
    }


def _sorted_unique(values: Iterable[Any]) -> list[str]:
    return sorted({str(value) for value in values if str(value).strip()})


def _ordered_unique(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _add_sorted_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
        values.sort()


def _issue(
    code: FrontendDesignErrorCode,
    message: str,
    phase: str,
    blame_symbol: str,
    **context: Any,
) -> FrontendDesignIssue:
    return FrontendDesignIssue(
        code=code,
        message=message,
        phase=phase,
        blame_symbol=blame_symbol,
        context=context,
    )


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() not in {"0", "false", "no", "off"}
