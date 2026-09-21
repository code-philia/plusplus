"""Frontend Design state, validation, and requirement-level UI scope planning."""

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

from .frontend_ir import (
    FRONTEND_DESIGN_IR_SCHEMA,
    FRONTEND_IR_SCHEMA_VERSION,
    NAVIGATION_SCHEMA,
    RENDER_OBLIGATION_SCHEMA,
    SEMANTIC_FIELD_SCHEMA,
    STORE_ACTION_SCHEMA,
    STORE_PERSISTENCE_SCHEMA,
    FrontendDesignErrorCode,
    FrontendDesignIssue,
    repair_schema_shape,
    schema_shape_errors,
)
from .trace_payload import format_payload_trace
from .model_client import StructuredModel, describe_model_error


_SYMBOL_PREFIXES = {
    "PAGE": "PAGE",
    "LAYOUT": "LAYOUT",
    "COMPONENT": "COMPONENT",
    "STORE": "STORE",
}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _string_list(*, max_items: int = 64, max_length: int = 240) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": max_items,
        "items": {"type": "string", "minLength": 1, "maxLength": max_length},
    }


_ACTION_SCHEMA = {"type": "string", "enum": ["CREATE", "REUSE"]}

UI_SCOPE_LAYOUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action",
        "name",
        "spec",
        "render_obligations",
        "visual_reference_ids",
    ],
    "properties": {
        "action": _ACTION_SCHEMA,
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "spec": {"type": "string", "maxLength": 800},
        "render_obligations": {"type": "array", "items": RENDER_OBLIGATION_SCHEMA},
        "visual_reference_ids": _string_list(),
    },
}

UI_SCOPE_STORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "name", "spec", "state", "actions", "persistence"],
    "properties": {
        "action": _ACTION_SCHEMA,
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "spec": {"type": "string", "maxLength": 800},
        "state": {"type": "array", "items": SEMANTIC_FIELD_SCHEMA},
        "actions": {"type": "array", "items": STORE_ACTION_SCHEMA},
        "persistence": STORE_PERSISTENCE_SCHEMA,
    },
}

UI_SCOPE_PAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action",
        "name",
        "spec",
        "route",
        "route_inputs",
        "layout_name",
        "api_dependencies",
        "store_names",
        "render_obligations",
        "navigation",
        "visual_reference_ids",
    ],
    "properties": {
        "action": _ACTION_SCHEMA,
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "spec": {"type": "string", "maxLength": 800},
        "route": {"type": "string", "maxLength": 240},
        "route_inputs": {"type": "array", "items": SEMANTIC_FIELD_SCHEMA},
        "layout_name": _nullable(
            {"type": "string", "minLength": 1, "maxLength": 80}
        ),
        "api_dependencies": _string_list(),
        "store_names": _string_list(),
        "render_obligations": {"type": "array", "items": RENDER_OBLIGATION_SCHEMA},
        "navigation": {"type": "array", "items": NAVIGATION_SCHEMA},
        "visual_reference_ids": _string_list(),
    },
}

REQUIREMENT_UI_SCOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requirement_id", "ui_scope", "layouts", "stores", "pages"],
    "properties": {
        "requirement_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "ui_scope": {
            "type": "string",
            "enum": ["UI_REQUIRED", "UI_AFFECTING", "NO_UI"],
        },
        "layouts": {
            "type": "array",
            "maxItems": 8,
            "items": UI_SCOPE_LAYOUT_SCHEMA,
        },
        "stores": {
            "type": "array",
            "maxItems": 8,
            "items": UI_SCOPE_STORE_SCHEMA,
        },
        "pages": {
            "type": "array",
            "maxItems": 8,
            "items": UI_SCOPE_PAGE_SCHEMA,
        },
    },
}


UI_SCOPE_INSTRUCTIONS = """Classify one requirement node and plan only its page, layout, and store scope.
Return exactly the supplied JSON shape. Use UI_REQUIRED when the requirement directly needs a user-facing page,
UI_AFFECTING when it changes or reuses an existing UI symbol without introducing a page, and NO_UI only when no UI
symbol is involved. UI_REQUIRED must include at least one CREATE or REUSE page. UI_AFFECTING must include at least
one CREATE or REUSE layout, store, or page. Never return UI_REQUIRED or UI_AFFECTING with all corresponding symbol
arrays empty. CREATE allocates a new global symbol; REUSE must name a symbol already present in the registry.
Never create a second symbol with an existing name. Names are global English symbol names and must be stable across
requirements. A CREATE page needs a non-empty absolute route and spec. A CREATE layout or store needs a non-empty
spec. A CREATE store must define only genuinely cross-page state and its public actions; do not move page-local form
or loading state into a global store. Every CREATE store must choose an explicit persistence strategy. Use MEMORY for
state that may disappear on reload. Use LOCAL_STORAGE with a stable product-specific storage_key when the requirement
states that authentication or other state survives reload/browser navigation. Never claim reload persistence in prose
while returning MEMORY. A semantic_id is a global typed identity: whenever Store state reuses a
semantic_id from requirement_contract or backend_apis, copy both its canonical name and type exactly. If Store state
has a different lifecycle type, such as a nullable current-session projection of a required registration field, it
is a different semantic value and must use a Store-domain id such as session.username or auth.current_username;
never reuse traveler.username with a changed type. `required` controls property presence and does not make the value
nullable; nullability belongs only in `type`. For REUSE, keep create-only fields as empty strings, empty arrays, or null; the
compiler ignores them. Refer to an existing symbol by copying its registry `name` exactly; do not place the
PAGE/LAYOUT/STORE prefix in `name`. Page and Layout specs must describe the intended information hierarchy,
major regions, content density, and the responsibility of each referenced visual region so later implementation can
translate the supplied layout/style evidence faithfully. Preserve the product's own requirement content: reference
images guide composition and visual language, not unrelated data. Refer
only to Backend API ids and visual reference ids supplied for this requirement. API ids are opaque and must be copied
exactly. Every navigation edge must provide a concrete absolute target_route. Use "/" for the compiler-owned system
main interface when a requirement says Home, HomePage, main screen, or equivalent and no requirement-owned home page
is being created. FOLDER requirements are processed after their children: prefer REUSE for child pages already present in the
registry, and CREATE only UI structure directly required by the folder's own description. FOLDER requirements may
legitimately receive no Requirement Contract or Backend API. Do not design components, JSX, CSS, files,
implementation logic, local store fields, or API bindings. Use [] whenever a list is empty and return only the
structured JSON object.
"""


@dataclass(slots=True)
class RequirementUIScopeResult:
    frontend_ir: dict[str, Any]
    node_states: dict[str, str]
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class FrontendDesignState:
    """Transactional registry for the global Frontend Design symbol graph."""

    def __init__(self, visual_references: Iterable[dict[str, Any]] = ()) -> None:
        self.visual_references: dict[str, dict[str, Any]] = {
            str(item.get("id")): copy.deepcopy(item)
            for item in visual_references
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }
        self.layouts: dict[str, dict[str, Any]] = {}
        self.pages: dict[str, dict[str, Any]] = {}
        self.components: dict[str, dict[str, Any]] = {}
        self.stores: dict[str, dict[str, Any]] = {}
        self.api_dependencies: list[dict[str, Any]] = []
        self.requirement_links: dict[str, dict[str, Any]] = {}

    def clone(self) -> "FrontendDesignState":
        return copy.deepcopy(self)

    @staticmethod
    def stable_symbol_id(kind: str, name: str) -> str:
        normalized_kind = str(kind).strip().upper()
        prefix = _SYMBOL_PREFIXES.get(normalized_kind)
        if prefix is None:
            raise ValueError(f"Unsupported Frontend symbol kind: {kind!r}")
        parts = re.findall(r"[A-Za-z0-9]+", str(name))
        if not parts:
            raise ValueError(f"Frontend symbol name has no ASCII identifier characters: {name!r}")
        stable_name = "".join(part[:1].upper() + part[1:] for part in parts)
        if stable_name[0].isdigit():
            stable_name = f"Ui{stable_name}"
        return f"{prefix}.{stable_name}"

    @classmethod
    def from_ir(cls, frontend_ir: dict[str, Any]) -> "FrontendDesignState":
        state = cls(frontend_ir.get("visual_references", []))
        for table_name in (
            "layouts",
            "pages",
            "components",
            "stores",
        ):
            table = getattr(state, table_name)
            for item in frontend_ir.get(table_name, []):
                if isinstance(item, dict) and str(item.get("id", "")).strip():
                    table[str(item["id"])] = copy.deepcopy(item)
        state.api_dependencies = copy.deepcopy(frontend_ir.get("api_dependencies", []))
        state.requirement_links = {
            str(item.get("requirement_id")): copy.deepcopy(item)
            for item in frontend_ir.get("requirement_links", [])
            if isinstance(item, dict) and str(item.get("requirement_id", "")).strip()
        }
        return state

    def to_ir(self) -> dict[str, Any]:
        def rows(table: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
            return [copy.deepcopy(table[key]) for key in sorted(table)]

        return {
            "schema_version": FRONTEND_IR_SCHEMA_VERSION,
            "visual_references": rows(self.visual_references),
            "layouts": rows(self.layouts),
            "pages": rows(self.pages),
            "components": rows(self.components),
            "stores": rows(self.stores),
            "api_dependencies": sorted(
                copy.deepcopy(self.api_dependencies),
                key=lambda item: (str(item.get("consumer_id", "")), str(item.get("api_id", ""))),
            ),
            "requirement_links": [
                copy.deepcopy(self.requirement_links[key])
                for key in sorted(self.requirement_links)
            ],
        }

    def apply_scope_decision(
        self,
        decision: dict[str, Any],
        *,
        requirement_id: str,
        allowed_api_ids: set[str],
        allowed_visual_ids: set[str],
    ) -> list[FrontendDesignIssue]:
        """Apply one model decision atomically; leave this state unchanged on failure."""

        issues = _scope_decision_issues(
            decision,
            requirement_id=requirement_id,
            state=self,
            allowed_api_ids=allowed_api_ids,
            allowed_visual_ids=allowed_visual_ids,
        )
        if issues:
            return issues

        trial = self.clone()
        linked_symbols: set[str] = set()
        linked_visuals: set[str] = set()

        for item in decision["layouts"]:
            symbol_id = self.stable_symbol_id("LAYOUT", item["name"])
            linked_symbols.add(symbol_id)
            linked_visuals.update(str(value) for value in item["visual_reference_ids"])
            if item["action"] == "CREATE":
                trial.layouts[symbol_id] = {
                    "id": symbol_id,
                    "spec": str(item["spec"]).strip(),
                    "requirement_ids": [requirement_id],
                    "component_ids": [],
                    "render_obligations": copy.deepcopy(item["render_obligations"]),
                    "visual_reference_ids": sorted(set(item["visual_reference_ids"])),
                }
            else:
                _add_sorted_unique(trial.layouts[symbol_id]["requirement_ids"], requirement_id)

        for item in decision["stores"]:
            symbol_id = self.stable_symbol_id("STORE", item["name"])
            linked_symbols.add(symbol_id)
            if item["action"] == "CREATE":
                trial.stores[symbol_id] = {
                    "id": symbol_id,
                    "spec": str(item["spec"]).strip(),
                    "state": copy.deepcopy(item["state"]),
                    "actions": copy.deepcopy(item["actions"]),
                    "persistence": copy.deepcopy(item["persistence"]),
                    "requirement_ids": [requirement_id],
                }
            else:
                _add_sorted_unique(trial.stores[symbol_id]["requirement_ids"], requirement_id)

        for item in decision["pages"]:
            page_id = self.stable_symbol_id("PAGE", item["name"])
            linked_symbols.add(page_id)
            linked_visuals.update(str(value) for value in item["visual_reference_ids"])
            if item["action"] == "CREATE":
                layout_id = (
                    self.stable_symbol_id("LAYOUT", item["layout_name"])
                    if item["layout_name"] is not None
                    else None
                )
                store_ids = sorted(
                    {self.stable_symbol_id("STORE", name) for name in item["store_names"]}
                )
                if layout_id is not None:
                    linked_symbols.add(layout_id)
                linked_symbols.update(store_ids)
                trial.pages[page_id] = {
                    "id": page_id,
                    "spec": str(item["spec"]).strip(),
                    "route": str(item["route"]).strip(),
                    "route_inputs": copy.deepcopy(item["route_inputs"]),
                    "requirement_ids": [requirement_id],
                    "layout_id": layout_id,
                    "component_ids": [],
                    "api_dependencies": list(dict.fromkeys(item["api_dependencies"])),
                    "store_dependencies": store_ids,
                    "render_obligations": copy.deepcopy(item["render_obligations"]),
                    "navigation": copy.deepcopy(item["navigation"]),
                    "visual_reference_ids": sorted(set(item["visual_reference_ids"])),
                }
            else:
                _add_sorted_unique(trial.pages[page_id]["requirement_ids"], requirement_id)
                existing_page = trial.pages[page_id]
                if existing_page.get("layout_id"):
                    linked_symbols.add(str(existing_page["layout_id"]))
                linked_symbols.update(str(value) for value in existing_page["store_dependencies"])

        trial.requirement_links[requirement_id] = {
            "requirement_id": requirement_id,
            "ui_scope": decision["ui_scope"],
            "symbol_ids": sorted(linked_symbols),
            "visual_reference_ids": sorted(linked_visuals),
        }

        self.visual_references = trial.visual_references
        self.layouts = trial.layouts
        self.pages = trial.pages
        self.components = trial.components
        self.stores = trial.stores
        self.api_dependencies = trial.api_dependencies
        self.requirement_links = trial.requirement_links
        return []


class RequirementUIScopePass:
    """Plan Requirement-to-Page/Layout/Store scope synchronously and serially."""

    def __init__(self, model: StructuredModel, artifact_root: Path) -> None:
        self._model = model
        arc_root = artifact_root.expanduser().resolve()
        self._log = SynchronousLog("RequirementUIScopePass", workspace_root=arc_root.parent)
        self._retry_count = _bounded_env_int("ARC_STRUCTURED_OUTPUT_RETRY_COUNT", 2, 0, 10)
        self._trace_enabled = _env_flag("ARC_FRONTEND_DESIGN_TRACE", True)

    def compile(
        self,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any],
        backend_design_ir: dict[str, Any],
        visual_references: list[dict[str, Any]],
    ) -> RequirementUIScopeResult:
        nodes = requirement_ir.get("nodes", {})
        requirement_ids = {
            str(value)
            for value in requirement_ir.get("node_order", [])
            if str(value).strip() and str(value) in nodes
        }
        if not requirement_ids:
            requirement_ids = {str(value) for value in nodes if str(value).strip()}
        state = FrontendDesignState(visual_references)
        node_states: dict[str, str] = {}
        all_issues: list[FrontendDesignIssue] = []
        contracts = _requirement_contracts(backend_design_ir)
        apis = _backend_apis(backend_design_ir)
        atomic_dependencies = dependency_graph.get("atomic_dependencies", {})
        requirement_order = _requirement_order(requirement_ids, dependency_graph)

        for requirement_id in requirement_order:
            requirement = copy.deepcopy(nodes.get(requirement_id, {}))
            requirement["requirement_id"] = requirement_id
            is_atomic = str(requirement.get("type", "")).upper() == "ATOMIC"
            allowed_owner_ids = (
                {requirement_id}
                | _dependency_closure(requirement_id, atomic_dependencies)
                if is_atomic
                else set()
            )
            allowed_apis = {
                module_id: module
                for module_id, module in apis.items()
                if _api_owner(module_id, module) in allowed_owner_ids
            }
            requirement_visuals = [
                copy.deepcopy(item)
                for item in visual_references
                if isinstance(item, dict)
                and requirement_id in {
                    str(value) for value in item.get("requirement_ids", [])
                }
            ]
            if is_atomic and requirement_id not in contracts:
                issue = _issue(
                    FrontendDesignErrorCode.REFERENCE_UNKNOWN,
                    f"Backend Design has no Requirement Contract for {requirement_id}.",
                    "REQUIREMENT_UI_SCOPE",
                    requirement_id,
                )
                node_states[requirement_id] = "FAILED"
                all_issues.append(issue)
                break
            decision, issues = self._decide(
                requirement_id=requirement_id,
                requirement=requirement,
                requirement_contract=contracts.get(requirement_id),
                backend_apis=allowed_apis,
                visual_references=requirement_visuals,
                state=state,
            )
            if decision is None:
                node_states[requirement_id] = "FAILED"
                all_issues.extend(issues)
                break

            apply_issues = state.apply_scope_decision(
                decision,
                requirement_id=requirement_id,
                allowed_api_ids=set(allowed_apis),
                allowed_visual_ids={str(item.get("id")) for item in requirement_visuals},
            )
            if apply_issues:
                node_states[requirement_id] = "FAILED"
                all_issues.extend(apply_issues)
                break
            node_states[requirement_id] = (
                "UI_NOT_REQUIRED"
                if decision["ui_scope"] == "NO_UI"
                else "UI_SCOPE_PLANNED"
            )

        frontend_ir = state.to_ir()
        if not all_issues and set(state.requirement_links) == requirement_ids:
            validation_issues = validate_frontend_design_minimum(
                frontend_ir,
                expected_requirement_ids=requirement_ids,
                backend_api_ids=set(apis),
            )
            all_issues.extend(validation_issues)
            if validation_issues and requirement_order:
                blamed_requirement = next(
                    (
                        issue.blame_symbol
                        for issue in validation_issues
                        if issue.blame_symbol in node_states
                    ),
                    requirement_order[-1],
                )
                node_states[blamed_requirement] = "FAILED"

        return RequirementUIScopeResult(
            frontend_ir=frontend_ir,
            node_states=node_states,
            errors=[issue.format() for issue in all_issues],
        )

    def _decide(
        self,
        *,
        requirement_id: str,
        requirement: dict[str, Any],
        requirement_contract: dict[str, Any] | None,
        backend_apis: dict[str, dict[str, Any]],
        visual_references: list[dict[str, Any]],
        state: FrontendDesignState,
    ) -> tuple[dict[str, Any] | None, list[FrontendDesignIssue]]:
        feedback: list[str] = []
        last_raw_decision: Any = {}
        canonical_fields = _canonical_semantic_fields(
            requirement_contract,
            backend_apis.values(),
        )
        for attempt in range(self._retry_count + 1):
            payload = {
                "requirement": _model_requirement(requirement),
                "requirement_contract": copy.deepcopy(requirement_contract),
                "backend_apis": [
                    _model_api(backend_apis[key]) for key in sorted(backend_apis)
                ],
                "visual_references": [
                    _model_visual(item) for item in sorted(
                        visual_references,
                        key=lambda value: str(value.get("id", "")),
                    )
                ],
                "existing_ui_registry": _model_registry(state),
                "semantic_identity_policy": {
                    "same_semantic_id_requires_same_name_and_type": True,
                    "nullable_store_projection_requires_distinct_semantic_id": True,
                    "canonical_fields": [
                        canonical_fields[key] for key in sorted(canonical_fields)
                    ],
                },
            }
            if feedback:
                payload["validation_feedback"] = feedback
            self._trace(
                f"MODEL_REQUEST phase=requirement_ui_scope unit={requirement_id} "
                f"attempt={attempt + 1}/{self._retry_count + 1}"
            )
            provider_schema = _provider_output_schema(REQUIREMENT_UI_SCOPE_SCHEMA)
            self._trace_json(
                "MODEL_INPUT",
                "requirement_ui_scope",
                requirement_id,
                {
                    "schema_name": "arc_requirement_ui_scope",
                    "instructions": UI_SCOPE_INSTRUCTIONS,
                    "input_payload": payload,
                    "output_schema": provider_schema,
                    "local_validation_schema": REQUIREMENT_UI_SCOPE_SCHEMA,
                },
            )
            started = time.perf_counter()
            try:
                raw_decision = self._model.generate_json(
                    schema_name="arc_requirement_ui_scope",
                    instructions=UI_SCOPE_INSTRUCTIONS,
                    input_payload=payload,
                    output_schema=provider_schema,
                )
                last_raw_decision = raw_decision
            except Exception as exc:
                detail = describe_model_error(exc)
                issue = _issue(
                    FrontendDesignErrorCode.UI_SCOPE_MODEL_FAILED,
                    f"UI scope model call failed: {detail}",
                    "REQUIREMENT_UI_SCOPE",
                    requirement_id,
                )
                feedback = [issue.format()]
                self._trace(
                    f"MODEL_ERROR phase=requirement_ui_scope unit={requirement_id} "
                    f"error={detail}"
                )
                continue

            duration = int((time.perf_counter() - started) * 1000)
            self._trace_json(
                "MODEL_OUTPUT",
                "requirement_ui_scope",
                requirement_id,
                raw_decision,
                duration,
            )
            decision = _repair_scope_decision(
                raw_decision,
                requirement_id=requirement_id,
                requirement=requirement,
                state=state,
                allowed_api_ids=set(backend_apis),
                allowed_visual_ids={str(item.get("id")) for item in visual_references},
                canonical_fields=canonical_fields,
            )
            if decision != raw_decision:
                self._trace(
                    f"MODEL_REPAIRED phase=requirement_ui_scope unit={requirement_id} "
                    f"attempt={attempt + 1}"
                )
                self._trace_json(
                    "MODEL_REPAIRED_OUTPUT",
                    "requirement_ui_scope",
                    requirement_id,
                    decision,
                )
            validation_issues = _scope_decision_issues(
                decision,
                requirement_id=requirement_id,
                state=state,
                allowed_api_ids=set(backend_apis),
                allowed_visual_ids={str(item.get("id")) for item in visual_references},
                canonical_fields=canonical_fields,
            )
            if not validation_issues:
                self._trace(
                    f"MODEL_ACCEPTED phase=requirement_ui_scope unit={requirement_id} "
                    f"attempt={attempt + 1} duration_ms={duration}"
                )
                return decision, []
            feedback = [issue.format() for issue in validation_issues]
            self._trace(
                f"MODEL_REJECTED phase=requirement_ui_scope unit={requirement_id} "
                f"errors={'; '.join(feedback)}"
            )

        fallback = _repair_scope_decision(
            last_raw_decision,
            requirement_id=requirement_id,
            requirement=requirement,
            state=state,
            allowed_api_ids=set(backend_apis),
            allowed_visual_ids={str(item.get("id")) for item in visual_references},
            canonical_fields=canonical_fields,
        )
        fallback_issues = _scope_decision_issues(
            fallback,
            requirement_id=requirement_id,
            state=state,
            allowed_api_ids=set(backend_apis),
            allowed_visual_ids={str(item.get("id")) for item in visual_references},
            canonical_fields=canonical_fields,
        )
        if not fallback_issues:
            self._trace(
                f"MODEL_FALLBACK phase=requirement_ui_scope unit={requirement_id} "
                "decision=minimum_valid_scope"
            )
            return fallback, []
        return None, fallback_issues

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
            f"{format_payload_trace(payload)}"
        )


def validate_frontend_design_minimum(
    frontend_ir: dict[str, Any],
    *,
    expected_requirement_ids: set[str] | None = None,
    backend_api_ids: set[str] | None = None,
) -> list[FrontendDesignIssue]:
    """Validate only the stable shape and references needed by lowering.

    Reverse indexes, semantic completeness, graph ordering, and full binding
    coverage are intentionally not Design-stage blockers. Those properties can
    be derived or completed by later frontend lowering and implementation.
    """

    shape_errors = schema_shape_errors(frontend_ir, FRONTEND_DESIGN_IR_SCHEMA)
    if shape_errors:
        return [
            _issue(
                FrontendDesignErrorCode.IR_INVALID,
                message,
                "FRONTEND_IR_VALIDATION",
                "<frontend-design-ir>",
            )
            for message in shape_errors
        ]

    issues: list[FrontendDesignIssue] = []
    tables = ("layouts", "pages", "components", "stores")
    symbols: dict[str, str] = {}
    for table_name in tables:
        for item in frontend_ir[table_name]:
            symbol_id = str(item["id"])
            if symbol_id in symbols:
                issues.append(_issue(
                    FrontendDesignErrorCode.SYMBOL_DUPLICATE,
                    f"Duplicate Frontend symbol id: {symbol_id}.",
                    "FRONTEND_IR_VALIDATION",
                    symbol_id,
                ))
            else:
                symbols[symbol_id] = table_name

    visual_ids = [str(item["id"]) for item in frontend_ir["visual_references"]]
    if len(visual_ids) != len(set(visual_ids)):
        issues.append(_issue(
            FrontendDesignErrorCode.SYMBOL_DUPLICATE,
            "Frontend visual reference ids must be unique.",
            "FRONTEND_IR_VALIDATION",
            "<visual-references>",
        ))
    known_visuals = set(visual_ids)
    known_pages = {str(item["id"]) for item in frontend_ir["pages"]}
    known_layouts = {str(item["id"]) for item in frontend_ir["layouts"]}
    known_components = {str(item["id"]) for item in frontend_ir["components"]}
    known_stores = {str(item["id"]) for item in frontend_ir["stores"]}

    links: dict[str, dict[str, Any]] = {}
    for link in frontend_ir["requirement_links"]:
        requirement_id = str(link["requirement_id"])
        if requirement_id in links:
            issues.append(_issue(
                FrontendDesignErrorCode.SYMBOL_DUPLICATE,
                f"Duplicate Frontend requirement link: {requirement_id}.",
                "FRONTEND_IR_VALIDATION",
                requirement_id,
            ))
        links[requirement_id] = link
        for symbol_id in link["symbol_ids"]:
            if str(symbol_id) not in symbols:
                issues.append(_unknown_reference(requirement_id, "UI symbol", str(symbol_id)))
        for visual_id in link["visual_reference_ids"]:
            if str(visual_id) not in known_visuals:
                issues.append(_unknown_reference(requirement_id, "visual reference", str(visual_id)))
    if expected_requirement_ids is not None and set(links) != expected_requirement_ids:
        issues.append(_issue(
            FrontendDesignErrorCode.REQUIREMENT_UNCOVERED,
            "Frontend requirement links do not match the current requirement nodes.",
            "FRONTEND_IR_VALIDATION",
            "<requirement-links>",
            missing=sorted(expected_requirement_ids - set(links)),
            extra=sorted(set(links) - expected_requirement_ids),
        ))

    routes: dict[str, str] = {}
    for layout in frontend_ir["layouts"]:
        layout_id = str(layout["id"])
        for component_id in layout["component_ids"]:
            if str(component_id) not in known_components:
                issues.append(_unknown_reference(layout_id, "component", str(component_id)))
    for page in frontend_ir["pages"]:
        page_id = str(page["id"])
        route = str(page["route"])
        if route in routes and routes[route] != page_id:
            issues.append(_issue(
                FrontendDesignErrorCode.ROUTE_CONFLICT,
                f"Route {route!r} is owned by both {routes[route]} and {page_id}.",
                "FRONTEND_IR_VALIDATION",
                page_id,
            ))
        routes[route] = page_id
        layout_id = page.get("layout_id")
        if layout_id is not None and str(layout_id) not in known_layouts:
            issues.append(_unknown_reference(page_id, "layout", str(layout_id)))
        for component_id in page["component_ids"]:
            if str(component_id) not in known_components:
                issues.append(_unknown_reference(page_id, "component", str(component_id)))
        for store_id in page["store_dependencies"]:
            if str(store_id) not in known_stores:
                issues.append(_unknown_reference(page_id, "store", str(store_id)))
        for api_id in page["api_dependencies"]:
            if backend_api_ids is not None and str(api_id) not in backend_api_ids:
                issues.append(_unknown_reference(page_id, "Backend API", str(api_id)))

    available_routes = set(routes) | {"/"}
    for page in frontend_ir["pages"]:
        page_id = str(page["id"])
        for navigation in page.get("navigation", []):
            target_route = str(navigation.get("target_route", ""))
            if target_route not in available_routes:
                issues.append(_issue(
                    FrontendDesignErrorCode.REFERENCE_UNKNOWN,
                    f"{page_id} navigation targets unavailable route {target_route!r}.",
                    "FRONTEND_IR_VALIDATION",
                    page_id,
                    target_route=target_route,
                ))

    for component in frontend_ir["components"]:
        component_id = str(component["id"])
        page_owner = component.get("owner_page_id")
        layout_owner = component.get("owner_layout_id")
        if page_owner is not None and str(page_owner) not in known_pages:
            issues.append(_unknown_reference(component_id, "owner page", str(page_owner)))
        if layout_owner is not None and str(layout_owner) not in known_layouts:
            issues.append(_unknown_reference(component_id, "owner layout", str(layout_owner)))
    for dependency in frontend_ir["api_dependencies"]:
        consumer_id = str(dependency["consumer_id"])
        api_id = str(dependency["api_id"])
        if consumer_id not in symbols:
            issues.append(_unknown_reference(consumer_id, "API consumer", consumer_id))
        if backend_api_ids is not None and api_id not in backend_api_ids:
            issues.append(_unknown_reference(consumer_id, "Backend API", api_id))

    for table_name in ("layouts", "pages", "components"):
        for item in frontend_ir[table_name]:
            owner_id = str(item["id"])
            for visual_id in item["visual_reference_ids"]:
                if str(visual_id) not in known_visuals:
                    issues.append(_unknown_reference(owner_id, "visual reference", str(visual_id)))
    return issues


def _semantic_collection_issues(
    fields: Iterable[dict[str, Any]],
    owner_id: str,
    label: str,
) -> list[FrontendDesignIssue]:
    seen: dict[str, tuple[str, str, bool]] = {}
    issues: list[FrontendDesignIssue] = []
    for field in fields:
        semantic_id = str(field["semantic_id"])
        signature = (
            str(field["name"]),
            str(field["type"]),
            bool(field["required"]),
        )
        if semantic_id in seen:
            detail = "with incompatible definitions" if seen[semantic_id] != signature else "more than once"
            issues.append(_issue(
                FrontendDesignErrorCode.SYMBOL_DUPLICATE,
                f"{owner_id} defines {label} {semantic_id} {detail}.",
                "FRONTEND_IR_VALIDATION",
                owner_id,
            ))
        else:
            seen[semantic_id] = signature
    return issues


def _scope_decision_issues(
    decision: Any,
    *,
    requirement_id: str,
    state: FrontendDesignState,
    allowed_api_ids: set[str],
    allowed_visual_ids: set[str],
    canonical_fields: dict[str, dict[str, str]] | None = None,
) -> list[FrontendDesignIssue]:
    shape_errors = schema_shape_errors(decision, REQUIREMENT_UI_SCOPE_SCHEMA)
    if shape_errors:
        return [
            _issue(
                FrontendDesignErrorCode.UI_SCOPE_DECISION_INVALID,
                message,
                "REQUIREMENT_UI_SCOPE",
                requirement_id,
            )
            for message in shape_errors
        ]
    assert isinstance(decision, dict)
    issues: list[FrontendDesignIssue] = []
    if decision["requirement_id"] != requirement_id:
        issues.append(_issue(
            FrontendDesignErrorCode.UI_SCOPE_DECISION_INVALID,
            f"Decision requirement_id must be {requirement_id!r}.",
            "REQUIREMENT_UI_SCOPE",
            requirement_id,
        ))

    entries = decision["layouts"] + decision["stores"] + decision["pages"]
    if decision["ui_scope"] == "NO_UI" and entries:
        issues.append(_issue(
            FrontendDesignErrorCode.UI_SCOPE_DECISION_INVALID,
            "NO_UI decisions must not create or reuse UI symbols.",
            "REQUIREMENT_UI_SCOPE",
            requirement_id,
        ))
    if decision["ui_scope"] == "UI_REQUIRED" and not decision["pages"]:
        issues.append(_issue(
            FrontendDesignErrorCode.UI_SCOPE_DECISION_INVALID,
            "UI_REQUIRED decisions must create or reuse at least one page.",
            "REQUIREMENT_UI_SCOPE",
            requirement_id,
        ))
    if decision["ui_scope"] == "UI_AFFECTING" and not entries:
        issues.append(_issue(
            FrontendDesignErrorCode.UI_SCOPE_DECISION_INVALID,
            "UI_AFFECTING decisions must create or reuse at least one UI symbol.",
            "REQUIREMENT_UI_SCOPE",
            requirement_id,
        ))
    if requirement_id in state.requirement_links:
        issues.append(_issue(
            FrontendDesignErrorCode.UI_SCOPE_CREATION_CONFLICT,
            f"Requirement {requirement_id} already has a Frontend Design decision.",
            "REQUIREMENT_UI_SCOPE",
            requirement_id,
        ))

    table_specs = (
        ("LAYOUT", decision["layouts"], state.layouts),
        ("STORE", decision["stores"], state.stores),
        ("PAGE", decision["pages"], state.pages),
    )
    planned_ids: dict[str, set[str]] = {"LAYOUT": set(), "STORE": set(), "PAGE": set()}
    for kind, items, registry in table_specs:
        for item in items:
            try:
                symbol_id = state.stable_symbol_id(kind, item["name"])
            except ValueError as exc:
                issues.append(_issue(
                    FrontendDesignErrorCode.UI_SCOPE_DECISION_INVALID,
                    str(exc),
                    "REQUIREMENT_UI_SCOPE",
                    requirement_id,
                ))
                continue
            if symbol_id in planned_ids[kind]:
                issues.append(_issue(
                    FrontendDesignErrorCode.UI_SCOPE_DECISION_INVALID,
                    f"Decision mentions {symbol_id} more than once.",
                    "REQUIREMENT_UI_SCOPE",
                    symbol_id,
                ))
            planned_ids[kind].add(symbol_id)
            if item["action"] == "CREATE":
                if symbol_id in registry:
                    issues.append(_issue(
                        FrontendDesignErrorCode.UI_SCOPE_CREATION_CONFLICT,
                        f"CREATE conflicts with existing symbol {symbol_id}; use REUSE.",
                        "REQUIREMENT_UI_SCOPE",
                        symbol_id,
                    ))
                if not str(item["spec"]).strip():
                    issues.append(_issue(
                        FrontendDesignErrorCode.UI_SCOPE_DECISION_INVALID,
                        f"CREATE {symbol_id} requires a non-empty spec.",
                        "REQUIREMENT_UI_SCOPE",
                        symbol_id,
                    ))
                if kind == "STORE":
                    issues.extend(_semantic_collection_issues(
                        item["state"],
                        symbol_id,
                        "state",
                    ))
                    issues.extend(_store_canonical_field_issues(
                        item["state"],
                        symbol_id,
                        canonical_fields or {},
                    ))
                    action_names = [str(value["name"]) for value in item["actions"]]
                    if len(action_names) != len(set(action_names)):
                        issues.append(_issue(
                            FrontendDesignErrorCode.UI_SCOPE_DECISION_INVALID,
                            f"CREATE {symbol_id} repeats Store action names.",
                            "REQUIREMENT_UI_SCOPE",
                            symbol_id,
                        ))
                    persistence = item.get("persistence", {})
                    persistence_kind = str(persistence.get("kind", ""))
                    storage_key = persistence.get("storage_key")
                    if persistence_kind == "LOCAL_STORAGE" and not str(storage_key or "").strip():
                        issues.append(_issue(
                            FrontendDesignErrorCode.UI_SCOPE_DECISION_INVALID,
                            f"CREATE {symbol_id} with LOCAL_STORAGE requires storage_key.",
                            "REQUIREMENT_UI_SCOPE",
                            symbol_id,
                        ))
                    if persistence_kind == "MEMORY" and storage_key is not None:
                        issues.append(_issue(
                            FrontendDesignErrorCode.UI_SCOPE_DECISION_INVALID,
                            f"CREATE {symbol_id} with MEMORY must set storage_key to null.",
                            "REQUIREMENT_UI_SCOPE",
                            symbol_id,
                        ))
            elif symbol_id not in registry:
                issues.append(_issue(
                    FrontendDesignErrorCode.UI_SCOPE_REUSE_MISSING,
                    f"REUSE references missing symbol {symbol_id}.",
                    "REQUIREMENT_UI_SCOPE",
                    symbol_id,
                ))

    available_layouts = set(state.layouts) | planned_ids["LAYOUT"]
    available_stores = set(state.stores) | planned_ids["STORE"]
    route_owner = {str(page["route"]): page_id for page_id, page in state.pages.items()}
    for item in decision["pages"]:
        page_id = _safe_stable_id(state, "PAGE", item["name"])
        if page_id is None:
            continue
        if item["action"] == "CREATE":
            route = str(item["route"]).strip()
            if not route.startswith("/"):
                issues.append(_issue(
                    FrontendDesignErrorCode.UI_SCOPE_DECISION_INVALID,
                    f"CREATE {page_id} requires an absolute route beginning with '/'.",
                    "REQUIREMENT_UI_SCOPE",
                    page_id,
                ))
            elif route in route_owner:
                issues.append(_issue(
                    FrontendDesignErrorCode.ROUTE_CONFLICT,
                    f"Route {route!r} is already owned by {route_owner[route]}.",
                    "REQUIREMENT_UI_SCOPE",
                    page_id,
                ))
            else:
                route_owner[route] = page_id
            if item["layout_name"] is not None:
                layout_id = _safe_stable_id(state, "LAYOUT", item["layout_name"])
                if layout_id is not None and layout_id not in available_layouts:
                    issues.append(_unknown_reference(
                        page_id,
                        "layout",
                        layout_id,
                        phase="REQUIREMENT_UI_SCOPE",
                    ))
            for store_name in item["store_names"]:
                store_id = _safe_stable_id(state, "STORE", store_name)
                if store_id is not None and store_id not in available_stores:
                    issues.append(_unknown_reference(
                        page_id,
                        "store",
                        store_id,
                        phase="REQUIREMENT_UI_SCOPE",
                    ))
            for navigation in item.get("navigation", []):
                target_route = str(navigation.get("target_route", ""))
                if not target_route.startswith("/"):
                    issues.append(_issue(
                        FrontendDesignErrorCode.UI_SCOPE_DECISION_INVALID,
                        f"CREATE {page_id} navigation requires an absolute target_route.",
                        "REQUIREMENT_UI_SCOPE",
                        page_id,
                    ))
            _validate_api_ids(
                issues,
                page_id,
                item["api_dependencies"],
                allowed_api_ids,
                phase="REQUIREMENT_UI_SCOPE",
            )

    visual_ids: list[str] = []
    for item in decision["layouts"]:
        visual_ids.extend(str(value) for value in item["visual_reference_ids"])
    for item in decision["pages"]:
        visual_ids.extend(str(value) for value in item["visual_reference_ids"])
    for visual_id in sorted(set(visual_ids)):
        if visual_id not in allowed_visual_ids:
            issues.append(_issue(
                FrontendDesignErrorCode.REFERENCE_UNKNOWN,
                f"Requirement {requirement_id} cannot use visual reference {visual_id}.",
                "REQUIREMENT_UI_SCOPE",
                requirement_id,
                visual_reference_id=visual_id,
            ))
    return issues


def _canonical_semantic_fields(
    requirement_contract: dict[str, Any] | None,
    backend_apis: Iterable[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Collect the authoritative typed identities visible to one UI decision."""

    result: dict[str, dict[str, str]] = {}
    owners: list[dict[str, Any]] = []
    if isinstance(requirement_contract, dict):
        owners.append(requirement_contract)
    owners.extend(value for value in backend_apis if isinstance(value, dict))
    for owner in owners:
        for field in [*owner.get("inputs", []), *owner.get("outputs", [])]:
            if not isinstance(field, dict):
                continue
            semantic_id = str(field.get("semantic_id", "")).strip()
            name = str(field.get("name", "")).strip()
            field_type = str(field.get("type", "")).strip()
            if semantic_id and name and field_type:
                result.setdefault(
                    semantic_id,
                    {
                        "semantic_id": semantic_id,
                        "name": name,
                        "type": field_type,
                    },
                )
    return result


def _store_canonical_field_issues(
    fields: Iterable[dict[str, Any]],
    store_id: str,
    canonical_fields: dict[str, dict[str, str]],
) -> list[FrontendDesignIssue]:
    issues: list[FrontendDesignIssue] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        semantic_id = str(field.get("semantic_id", "")).strip()
        canonical = canonical_fields.get(semantic_id)
        if canonical is None:
            continue
        actual_name = str(field.get("name", "")).strip()
        actual_type = str(field.get("type", "")).strip()
        if actual_name == canonical["name"] and actual_type == canonical["type"]:
            continue
        issues.append(_issue(
            FrontendDesignErrorCode.BINDING_INCOMPATIBLE,
            f"{store_id} reuses canonical semantic field {semantic_id} as "
            f"{actual_type!r}/{actual_name!r}; expected "
            f"{canonical['type']!r}/{canonical['name']!r}. A Store projection with "
            "different lifecycle or nullability must use a distinct Store-domain semantic_id.",
            "FRONTEND_SEMANTIC_VALIDATION",
            store_id,
        ))
    return issues


def _repair_store_semantic_ids(
    fields: Iterable[dict[str, Any]],
    store_name: str,
    canonical_fields: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Namespace lifecycle projections that are not the canonical contract value."""

    store_token = re.sub(r"(?<!^)(?=[A-Z])", "_", store_name).lower()
    store_token = re.sub(r"[^a-z0-9_]+", "_", store_token).strip("_") or "state"
    result: list[dict[str, Any]] = []
    for raw in fields:
        field = copy.deepcopy(raw)
        semantic_id = str(field.get("semantic_id", "")).strip()
        canonical = canonical_fields.get(semantic_id)
        if canonical is not None and (
            str(field.get("name", "")).strip() != canonical["name"]
            or str(field.get("type", "")).strip() != canonical["type"]
        ):
            field_name = re.sub(
                r"[^a-z0-9_]+",
                "_",
                str(field.get("name", "state")).strip().lower(),
            ).strip("_") or "state"
            field["semantic_id"] = f"store.{store_token}.{field_name}"
        result.append(field)
    return result


def _repair_scope_decision(
    decision: Any,
    *,
    requirement_id: str,
    requirement: dict[str, Any],
    state: FrontendDesignState,
    allowed_api_ids: set[str],
    allowed_visual_ids: set[str],
    canonical_fields: dict[str, dict[str, str]],
) -> Any:
    """Repair harmless UI-planning defects before enforcing graph invariants."""

    if not isinstance(decision, dict):
        return decision
    shaped = repair_schema_shape(decision, REQUIREMENT_UI_SCOPE_SCHEMA)
    if not isinstance(shaped, dict):
        return decision

    shaped["requirement_id"] = requirement_id
    raw_scope = str(shaped.get("ui_scope", "")).strip().upper()
    raw_layouts = shaped.get("layouts") if isinstance(shaped.get("layouts"), list) else []
    raw_stores = shaped.get("stores") if isinstance(shaped.get("stores"), list) else []
    raw_pages = shaped.get("pages") if isinstance(shaped.get("pages"), list) else []
    default_spec = _fallback_ui_spec(requirement, requirement_id)

    layouts: list[dict[str, Any]] = []
    layout_ids: set[str] = set(state.layouts)
    seen_layouts: set[str] = set()
    for index, raw in enumerate(raw_layouts[:8]):
        if not isinstance(raw, dict):
            continue
        name = _tolerant_symbol_name(
            raw.get("name"),
            _fallback_symbol_name(requirement_id, "Layout", index),
        )
        symbol_id = state.stable_symbol_id("LAYOUT", name)
        if symbol_id in seen_layouts:
            continue
        seen_layouts.add(symbol_id)
        exists = symbol_id in state.layouts
        layouts.append({
            "action": "REUSE" if exists else "CREATE",
            "name": name,
            "spec": "" if exists else _bounded_text(raw.get("spec"), 800, default_spec),
            "render_obligations": _unique_rows(
                _valid_schema_rows(raw.get("render_obligations"), RENDER_OBLIGATION_SCHEMA),
                "id",
            ),
            "visual_reference_ids": _allowed_strings(
                raw.get("visual_reference_ids"), allowed_visual_ids
            ),
        })
        layout_ids.add(symbol_id)

    stores: list[dict[str, Any]] = []
    store_ids: set[str] = set(state.stores)
    seen_stores: set[str] = set()
    for index, raw in enumerate(raw_stores[:8]):
        if not isinstance(raw, dict):
            continue
        name = _tolerant_symbol_name(
            raw.get("name"),
            _fallback_symbol_name(requirement_id, "Store", index),
        )
        symbol_id = state.stable_symbol_id("STORE", name)
        if symbol_id in seen_stores:
            continue
        seen_stores.add(symbol_id)
        exists = symbol_id in state.stores
        store_state = _repair_store_semantic_ids(
            _valid_schema_rows(raw.get("state"), SEMANTIC_FIELD_SCHEMA),
            name,
            canonical_fields,
        )
        stores.append({
            "action": "REUSE" if exists else "CREATE",
            "name": name,
            "spec": "" if exists else _bounded_text(raw.get("spec"), 800, default_spec),
            "state": _unique_rows(
                store_state,
                "semantic_id",
            ),
            "actions": _unique_rows(
                _valid_schema_rows(raw.get("actions"), STORE_ACTION_SCHEMA),
                "name",
            ),
            "persistence": _normalized_store_persistence(raw.get("persistence"), name),
        })
        store_ids.add(symbol_id)

    used_routes = {str(page.get("route", "")) for page in state.pages.values()}
    pages: list[dict[str, Any]] = []
    seen_pages: set[str] = set()
    for index, raw in enumerate(raw_pages[:8]):
        if not isinstance(raw, dict):
            continue
        name = _tolerant_symbol_name(
            raw.get("name"),
            _fallback_symbol_name(requirement_id, "Page", index),
        )
        page_id = state.stable_symbol_id("PAGE", name)
        if page_id in seen_pages:
            continue
        seen_pages.add(page_id)
        exists = page_id in state.pages

        layout_name: str | None = None
        if raw.get("layout_name") is not None:
            candidate_name = _tolerant_symbol_name(raw.get("layout_name"), "Layout")
            candidate_id = state.stable_symbol_id("LAYOUT", candidate_name)
            if candidate_id in layout_ids:
                layout_name = candidate_name

        store_names: list[str] = []
        for store_name in raw.get("store_names", []) if isinstance(raw.get("store_names"), list) else []:
            candidate_name = _tolerant_symbol_name(store_name, "Store")
            if state.stable_symbol_id("STORE", candidate_name) in store_ids:
                store_names.append(candidate_name)

        pages.append({
            "action": "REUSE" if exists else "CREATE",
            "name": name,
            "spec": "" if exists else _bounded_text(raw.get("spec"), 800, default_spec),
            "route": "" if exists else _unique_route(raw.get("route"), name, used_routes),
            "route_inputs": _unique_rows(
                _valid_schema_rows(raw.get("route_inputs"), SEMANTIC_FIELD_SCHEMA),
                "semantic_id",
            ),
            "layout_name": layout_name,
            "api_dependencies": _allowed_strings(raw.get("api_dependencies"), allowed_api_ids),
            "store_names": sorted(set(store_names)),
            "render_obligations": _unique_rows(
                _valid_schema_rows(raw.get("render_obligations"), RENDER_OBLIGATION_SCHEMA),
                "id",
            ),
            "navigation": _valid_schema_rows(raw.get("navigation"), NAVIGATION_SCHEMA),
            "visual_reference_ids": _allowed_strings(
                raw.get("visual_reference_ids"), allowed_visual_ids
            ),
        })

    has_entries = bool(layouts or stores or pages)
    scope = raw_scope if raw_scope in {"UI_REQUIRED", "UI_AFFECTING", "NO_UI"} else (
        "UI_REQUIRED" if pages else
        "UI_AFFECTING" if layouts or stores else
        "NO_UI"
    )
    # A valid model classification is semantic information, so never repair an
    # incomplete UI_REQUIRED/UI_AFFECTING decision by silently weakening it to
    # NO_UI. The cross-field validator will reject it and feed the defect back
    # to the model for another attempt.
    if scope == "NO_UI" and has_entries:
        scope = "UI_REQUIRED" if pages else "UI_AFFECTING"

    return {
        "requirement_id": requirement_id,
        "ui_scope": scope,
        "layouts": layouts,
        "stores": stores,
        "pages": pages,
    }


def _bounded_text(value: Any, maximum: int, fallback: str) -> str:
    text = str(value).strip() if value is not None else ""
    return (text or fallback)[:maximum]


def _fallback_ui_spec(requirement: dict[str, Any], requirement_id: str) -> str:
    parts = [
        str(requirement.get("name", "")).strip(),
        str(requirement.get("description", "")).strip(),
    ]
    return (" — ".join(value for value in parts if value) or f"UI for {requirement_id}.")[:800]


def _fallback_symbol_name(requirement_id: str, suffix: str, index: int) -> str:
    stem = "".join(
        part[:1].upper() + part[1:]
        for part in re.findall(r"[A-Za-z0-9]+", requirement_id)
    ) or "Requirement"
    ordinal = str(index + 1) if index else ""
    return f"{stem}{suffix}{ordinal}"[:80]


def _tolerant_symbol_name(value: Any, fallback: str) -> str:
    raw = str(value).strip() if value is not None else ""
    if "." in raw and raw.split(".", 1)[0].upper() in _SYMBOL_PREFIXES:
        raw = raw.split(".", 1)[1]
    parts = re.findall(r"[A-Za-z0-9]+", raw)
    name = "".join(part[:1].upper() + part[1:] for part in parts)
    if not name:
        name = fallback
    if name[0].isdigit():
        name = f"Ui{name}"
    return name[:80]


def _allowed_strings(values: Any, allowed: set[str]) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({str(value) for value in values if str(value) in allowed})


def _valid_schema_rows(values: Any, schema: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    result: list[dict[str, Any]] = []
    for value in values[:64]:
        repaired = repair_schema_shape(value, schema)
        if isinstance(repaired, dict) and not schema_shape_errors(repaired, schema):
            result.append(repaired)
    return result


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


def _unique_route(value: Any, name: str, used_routes: set[str]) -> str:
    route = str(value).strip() if value is not None else ""
    if not route:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "page"
        route = f"/{slug}"
    elif not route.startswith("/"):
        route = f"/{route}"
    route = route[:240]
    candidate = route
    index = 2
    while candidate in used_routes:
        suffix = f"-{index}"
        candidate = f"{route[:240 - len(suffix)]}{suffix}"
        index += 1
    used_routes.add(candidate)
    return candidate


def _requirement_contracts(backend_design_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): copy.deepcopy(item["contract"])
        for item in backend_design_ir.get("requirements", [])
        if isinstance(item, dict)
        and str(item.get("id", "")).strip()
        and isinstance(item.get("contract"), dict)
    }


def _backend_apis(backend_design_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): copy.deepcopy(item)
        for item in backend_design_ir.get("modules", [])
        if isinstance(item, dict)
        and item.get("kind") == "API"
        and str(item.get("id", "")).strip()
    }


def _api_owner(module_id: str, module: dict[str, Any]) -> str:
    owner = str(module.get("owner_requirement", "")).strip()
    return owner or module_id.split("::", 1)[0]


def _requirement_order(
    requirement_ids: set[str],
    dependency_graph: dict[str, Any],
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    waves = dependency_graph.get("implementation_waves", [])
    if isinstance(waves, list):
        for wave in waves:
            if not isinstance(wave, list):
                continue
            for requirement_id in sorted(str(value) for value in wave):
                if requirement_id in requirement_ids and requirement_id not in seen:
                    result.append(requirement_id)
                    seen.add(requirement_id)
    result.extend(sorted(requirement_ids - seen))
    return result


def _dependency_closure(requirement_id: str, dependencies: Any) -> set[str]:
    if not isinstance(dependencies, dict):
        return set()
    result: set[str] = set()
    pending = list(dependencies.get(requirement_id, []))
    while pending:
        current = str(pending.pop())
        if current in result:
            continue
        result.add(current)
        pending.extend(dependencies.get(current, []))
    return result


def _model_requirement(requirement: dict[str, Any]) -> dict[str, Any]:
    return {
        "requirement_id": str(requirement.get("requirement_id", "")),
        "name": str(requirement.get("name", "")),
        "type": str(requirement.get("type", "")),
        "description": str(requirement.get("description", "")),
        "parent_id": requirement.get("parent_id"),
        "children_ids": copy.deepcopy(requirement.get("children_ids", [])),
        "scenarios": copy.deepcopy(requirement.get("scenarios", [])),
    }


def _model_api(module: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(module.get(key, [] if key in {"inputs", "outputs"} else ""))
        for key in ("id", "spec", "inputs", "outputs")
    }


def _model_visual(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id", "")),
        "source_path": str(item.get("source_path", "")),
        "analysis": copy.deepcopy(item.get("analysis", {})),
    }


def _model_registry(state: FrontendDesignState) -> dict[str, Any]:
    return {
        "layouts": [
            {"id": item["id"], "name": str(item["id"]).split(".", 1)[-1], "spec": item["spec"]}
            for item in (state.layouts[key] for key in sorted(state.layouts))
        ],
        "pages": [
            {
                "id": item["id"],
                "name": str(item["id"]).split(".", 1)[-1],
                "spec": item["spec"],
                "route": item["route"],
                "layout_id": item["layout_id"],
                "store_dependencies": copy.deepcopy(item["store_dependencies"]),
            }
            for item in (state.pages[key] for key in sorted(state.pages))
        ],
        "stores": [
            {
                "id": item["id"],
                "name": str(item["id"]).split(".", 1)[-1],
                "spec": item["spec"],
                "state": copy.deepcopy(item["state"]),
                "actions": copy.deepcopy(item["actions"]),
                "persistence": copy.deepcopy(item["persistence"]),
            }
            for item in (state.stores[key] for key in sorted(state.stores))
        ],
    }


def _normalized_store_persistence(value: Any, store_name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        kind = str(value.get("kind", "")).upper()
        storage_key = value.get("storage_key")
        if kind == "LOCAL_STORAGE":
            key = str(storage_key or "").strip()
            if not key:
                key = f"arc.{_store_storage_segment(store_name)}"
            return {"kind": "LOCAL_STORAGE", "storage_key": key[:120]}
    return {"kind": "MEMORY", "storage_key": None}


def _store_storage_segment(value: str) -> str:
    words = [word.lower() for word in re.findall(r"[A-Za-z0-9]+", value)]
    return "-".join(words) or "store"


def _provider_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove annotation keywords rejected by some strict-schema providers."""

    unsupported = {"maxLength", "uniqueItems"}
    return {
        key: _provider_output_schema(value) if isinstance(value, dict) else [
            _provider_output_schema(item) if isinstance(item, dict) else item
            for item in value
        ] if isinstance(value, list) else value
        for key, value in schema.items()
        if key not in unsupported
    }


def _validate_api_ids(
    issues: list[FrontendDesignIssue],
    owner_id: str,
    values: Iterable[Any],
    allowed: set[str],
    *,
    phase: str = "FRONTEND_IR_VALIDATION",
) -> None:
    api_ids = [str(value) for value in values]
    if len(api_ids) != len(set(api_ids)):
        issues.append(_issue(
            FrontendDesignErrorCode.API_DEPENDENCY_INVALID,
            f"{owner_id} contains duplicate Backend API dependencies.",
            phase,
            owner_id,
        ))
    for api_id in api_ids:
        if api_id not in allowed:
            issues.append(_issue(
                FrontendDesignErrorCode.API_DEPENDENCY_INVALID,
                f"{owner_id} references unavailable Backend API {api_id}.",
                phase,
                owner_id,
                api_id=api_id,
            ))


def _unknown_reference(
    owner_id: str,
    label: str,
    reference: str,
    *,
    phase: str = "FRONTEND_IR_VALIDATION",
) -> FrontendDesignIssue:
    return _issue(
        FrontendDesignErrorCode.REFERENCE_UNKNOWN,
        f"{owner_id} references unknown {label}: {reference}.",
        phase,
        owner_id,
        reference=reference,
    )


def _safe_stable_id(
    state: FrontendDesignState,
    kind: str,
    name: str,
) -> str | None:
    try:
        return state.stable_symbol_id(kind, name)
    except ValueError:
        return None


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
