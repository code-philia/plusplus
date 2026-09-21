from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .base import BaseStructuredAgent, JsonModel
from .contracts import ProposedEdit, ProposedPatch


IMPLEMENTATION_AGENT_SCHEMA_VERSION = 1
ACTIONABLE_FAILURE_CLASSES = {
    "TYPE_CONTRACT",
    "IMPLEMENTATION_BEHAVIOR",
    "VISUAL_BEHAVIOR",
}


IMPLEMENTATION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "edits"],
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
        "edits": {
            "type": "array",
            "minItems": 1,
            "maxItems": 16,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["module_id", "replacement"],
                "properties": {
                    "module_id": {"type": "string", "minLength": 1, "maxLength": 300},
                    "replacement": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200000,
                    },
                },
            },
        },
    },
}


IMPLEMENTATION_INSTRUCTIONS = """You are ARC's bounded Implementation Agent.
Implement the smallest coherent code change for the supplied requirement and implementation mode.

Authority and evidence:
- Required behavior comes from requirement, scenarios, requirement_contract, and frozen_tests when present.
- Module behavior and reference-derived frontend layout/style evidence may come from design_context.
- Failure localization comes from failure_reports.
- Real files, symbols, call edges, and editable regions come from writable_targets.
- Read-only dependencies are context only and must never be edited.
- Source files are supplied in full so you can understand existing imports and public surfaces.
- The writable surface contains every editable module owned by this requirement, not only the first stack-frame match.
  Diagnose across callers, callees, and sibling modules before choosing the smallest coherent patch.

Hard scope rules:
- Return replacements only for module ids in allowed_writable_module_ids.
- Each replacement is only the text BETWEEN that module's ARC implementation markers.
- Do not include either implementation marker, an @arc-module marker, markdown fences, or a whole file.
- Do not modify tests, assertions, imports, exports, signatures, routes, generated types, configs, or compiler glue.
- Use only symbols already available in the supplied source file and public read-only interfaces.
- Do not invent files, modules, APIs, fields, routes, database tables, or requirement behavior.
- In TDD mode, do not weaken or work around frozen tests. In AGGREGATE mode, no frozen tests exist;
  requirement, design_context, and Code Binding ownership are authoritative.
- Do not mock ARC-owned modules. Mocking external systems is not part of this implementation patch.

Implementation rules:
- Address the supplied failure cluster, or the supplied aggregate scope in AGGREGATE mode, not future requirements.
- Prefer the smallest coherent vertical change and avoid speculative refactoring.
- Preserve async behavior, TypeScript types, observable UI behavior, and declared call edges.
- In a DB module, use the compiler-injected `database` Drizzle client together with the imported schema table symbols.
  READ/CREATE/UPDATE/DELETE effects must execute through database.select/insert/update/delete respectively. A table
  symbol is schema metadata, not a repository: never inspect rows, data, items, or arbitrary properties on it.
- Use the injected `eq`, `and`, and `or` Drizzle operators for predicates; do not emulate filtering after loading an
  entire table when the predicate can be expressed by the database.
- Never create another SQLite/Drizzle connection, replace persistence with a module-level array or object, or report a
  successful database write after merely constructing an id or return value. The injected client is the sole owner of
  the connection, including when DATABASE_URL is `:memory:` during tests.
- Seed fixtures are compiler-owned test setup. Never hard-code fixture records or `Seed data:` literals in a DB/FUNC/API
  implementation, and never make a read repository insert, synthesize, or return missing fixture rows. Tests must apply
  requirement.seed_fixtures through the compiler-owned seeding support before exercising application behavior.
- In an HTTP handler, map only expected requirement-level validation and conflict failures to 4xx responses. Rethrow
  unexpected database, runtime, and compiler-glue errors so the global error handler and test diagnostics preserve the
  real root cause; never disguise every exception as invalid user input.
- Every frontend Page, Layout, or Component edit must be a finished, responsive UI implementation, even when the
  current failure is classified as functional rather than visual. Do not stop at unstyled semantic markup.
- A generated UI skeleton may contain data-arc-obligation placeholders, but your replacement must remove those
  placeholders and render real semantic controls/content. A page that only returns labels, spans, or an empty shell is
  incomplete and must not be proposed as a successful implementation.
- The frontend styling system is Tailwind CSS v4 through @tailwindcss/vite. Use static Tailwind utility className
  strings inside the editable function-body region. Do not invent undefined semantic class names, add style tags, or use inline
  style objects when a Tailwind utility can express the design.
- Page, Layout, and Component markers contain the complete editable function body, so declare local state, effects,
  handlers, and the JSX return inside that region. Do not emit another function declaration.
- Page and Component implementation bodies receive compiler-wired references through `_dependencies`. Destructure and
  use its hooks, API clients, and runtime Stores; their business call parameters are intentionally left for this
  implementation step. Do not bypass an available client with ad-hoc fetch calls or create a second persistence store.
- Implement navigation using the exact design_context.frontend.screens[].navigation_targets[].target_route values. A Home or
  main-interface transition targets the compiler-owned system route `/`; after a successful action, navigate there.
- Store reload behavior is defined by design_context.frontend.shared_state_policies[].persistence. Keep transient state in MEMORY and
  implement reload-surviving state through the supplied LOCAL_STORAGE runtime Store and storage_key.
- For a Store target, replace only its runtime implementation region. Preserve its State, Actions, and Value interfaces
  and the compiler-owned persistence boundary around that region.
- Translate design_context into an internally coherent visual direction: content hierarchy, page composition,
  responsive containers, spacing rhythm, typography scale, palette, borders, surfaces, states, and one restrained
  signature detail appropriate to the product. Keep the direction consistent across all supplied frontend modules.
- Visual references are guidance for layout, style, and content hierarchy, not source data. Preserve requirement-owned
  labels, behavior, and records; never copy unrelated image content. If no reference is supplied, derive a deliberate
  domain-appropriate direction from the requirement instead of using a generic demo-page aesthetic.
- Preserve accessibility: semantic controls, associated labels, visible keyboard focus, sufficient contrast, and
  reduced-motion-safe behavior. Ensure navigation between declared pages remains discoverable and coherent.
- Treat screens and journeys as one connected product graph. Keep navigation, shared visual language, API usage, and
  cross-page state coherent across every supplied screen; choose layout and internal component decomposition here.
- If several supplied failures are consequences of the same root cause, fix that root cause once.
- A replacement must be complete source text for the inside of its implementation region.

Return exactly one JSON object and no prose:
{"summary":"short implementation intent","edits":[{"module_id":"exact writable id","replacement":"source inside markers"}]}
"""


@dataclass(frozen=True, slots=True)
class ImplementationRequest:
    requirement_id: str
    requirement: dict[str, Any]
    requirement_contract: dict[str, Any]
    test_manifest: dict[str, Any]
    code_binding_registry: dict[str, Any]
    failure_reports: tuple[Any, ...]
    iteration: int
    mode: str = "TDD"
    design_context: dict[str, Any] | None = None
    previous_patch_summary: dict[str, Any] | None = None


@dataclass(slots=True)
class ImplementationResult:
    requirement_id: str
    status: str
    patch: ProposedPatch | None = None
    summary: str = ""
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    schema_version: int = IMPLEMENTATION_AGENT_SCHEMA_VERSION

    @property
    def ok(self) -> bool:
        return self.status == "PATCH_PROPOSED" and self.patch is not None and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ImplementationAgent:
    """Produce one bounded implementation patch without owning any side effects."""

    def __init__(
        self,
        model: JsonModel,
        output_root: Path,
        *,
        retries: int = 2,
        max_context_characters: int = 600_000,
        trace: Callable[[str], None] | None = None,
    ) -> None:
        self.output_root = output_root.expanduser().resolve()
        self._max_context_characters = max(
            50_000,
            min(int(max_context_characters), 1_000_000),
        )
        self._agent = BaseStructuredAgent(
            model,
            schema_name="arc_implementation_patch",
            instructions=IMPLEMENTATION_INSTRUCTIONS,
            output_schema=IMPLEMENTATION_OUTPUT_SCHEMA,
            retries=retries,
            trace=trace,
        )

    def implement(self, request: ImplementationRequest) -> ImplementationResult:
        requirement_id = str(request.requirement_id).strip()
        context, source_hashes, focus_ids, errors = self._build_context(request)
        if errors:
            return ImplementationResult(
                requirement_id=requirement_id,
                status="CONTEXT_REJECTED",
                errors=errors,
            )

        invocation = self._agent.invoke(
            context,
            validate=lambda output: _validate_decision(
                output,
                allowed_ids=set(source_hashes),
                focus_ids=focus_ids,
            ),
        )
        if not invocation.ok or invocation.output is None:
            return ImplementationResult(
                requirement_id=requirement_id,
                status="MODEL_REJECTED",
                attempts=invocation.attempts,
                errors=invocation.errors,
            )

        edits = tuple(
            ProposedEdit(
                module_id=str(row["module_id"]),
                expected_sha256=source_hashes[str(row["module_id"])],
                replacement=str(row["replacement"]),
            )
            for row in invocation.output["edits"]
        )
        return ImplementationResult(
            requirement_id=requirement_id,
            status="PATCH_PROPOSED",
            patch=ProposedPatch(requirement_id=requirement_id, edits=edits),
            summary=str(invocation.output["summary"]),
            attempts=invocation.attempts,
        )

    def _build_context(
        self,
        request: ImplementationRequest,
    ) -> tuple[dict[str, Any], dict[str, str], set[str], list[str]]:
        requirement_id = str(request.requirement_id).strip()
        mode = str(request.mode).strip().upper()
        errors: list[str] = []
        if not requirement_id:
            errors.append("ARC4530 IMPLEMENTATION_CONTEXT_INVALID: requirement_id is required.")
        if not all(
            isinstance(value, dict)
            for value in (
                request.requirement,
                request.requirement_contract,
                request.test_manifest,
                request.code_binding_registry,
            )
        ):
            errors.append(
                "ARC4530 IMPLEMENTATION_CONTEXT_INVALID: requirement, contract, manifests, "
                "and bindings must be objects."
            )
            return {}, {}, set(), errors
        if not isinstance(request.iteration, int) or request.iteration < 1:
            errors.append(
                "ARC4530 IMPLEMENTATION_CONTEXT_INVALID: iteration must be positive."
            )
        requirement_payload_id = str(request.requirement.get("id", "")).strip()
        if requirement_payload_id and requirement_payload_id != requirement_id:
            errors.append(
                "ARC4530 IMPLEMENTATION_CONTEXT_INVALID: requirement payload id does not match."
            )
        if mode not in {"TDD", "AGGREGATE"}:
            errors.append(
                "ARC4530 IMPLEMENTATION_CONTEXT_INVALID: mode must be TDD or AGGREGATE."
            )
        if mode == "TDD" and request.test_manifest.get("status") != "TESTS_FROZEN":
            errors.append(
                "ARC4530 IMPLEMENTATION_CONTEXT_INVALID: Test Manifest is not frozen."
            )
        if request.code_binding_registry.get("status") != "CODE_BINDING_READY":
            errors.append(
                "ARC4530 IMPLEMENTATION_CONTEXT_INVALID: Code Binding Registry is not ready."
            )
        reports = [
            row
            for value in request.failure_reports
            if (row := _report_dict(value)) is not None
        ]
        if len(reports) != len(request.failure_reports):
            errors.append(
                "ARC4530 IMPLEMENTATION_CONTEXT_INVALID: every failure report must be serializable."
            )
        if not reports:
            errors.append(
                "ARC4530 IMPLEMENTATION_CONTEXT_INVALID: one failure cluster is required."
            )
        report_requirements = {
            str(row.get("requirement_id", "")) for row in reports if row
        }
        if report_requirements and report_requirements != {requirement_id}:
            errors.append(
                "ARC4530 IMPLEMENTATION_CONTEXT_INVALID: failure reports belong to another requirement."
            )
        failure_classes = {
            str(row.get("failure_class", "")) for row in reports if row
        }
        if len(failure_classes) != 1:
            errors.append(
                "ARC4530 IMPLEMENTATION_CONTEXT_INVALID: reports must share one failure class."
            )
        non_actionable = sorted(failure_classes - ACTIONABLE_FAILURE_CLASSES)
        if non_actionable:
            errors.append(
                "ARC4531 IMPLEMENTATION_FAILURE_NOT_ACTIONABLE: fixed workflow must handle "
                f"{non_actionable}."
            )
        fingerprints = {
            str(row.get("failure_fingerprint", ""))
            for row in reports
            if str(row.get("failure_fingerprint", ""))
        }
        if len(fingerprints) != 1:
            errors.append(
                "ARC4530 IMPLEMENTATION_CONTEXT_INVALID: reports must form one failure cluster."
            )

        requirement_targets = next(
            (
                row
                for row in request.code_binding_registry.get("requirement_targets", [])
                if isinstance(row, dict)
                and str(row.get("requirement_id", "")) == requirement_id
            ),
            None,
        )
        if requirement_targets is None:
            errors.append(
                f"ARC4530 IMPLEMENTATION_CONTEXT_INVALID: unknown requirement {requirement_id}."
            )
            return {}, {}, set(), errors
        bindings = {
            str(row.get("module_id", "")): row
            for row in request.code_binding_registry.get("code_bindings", [])
            if isinstance(row, dict) and str(row.get("module_id", ""))
        }
        writable_ids = {
            str(value) for value in requirement_targets.get("writable", []) if str(value)
        }
        read_only_ids = {
            str(value) for value in requirement_targets.get("read_only", []) if str(value)
        }
        reported_writable_ids = {
            str(target.get("module_id", ""))
            for report in reports
            for target in report.get("writable_targets", [])
            if isinstance(target, dict) and str(target.get("module_id", ""))
        }
        focus_ids = reported_writable_ids & writable_ids
        relevant_writable = set(writable_ids)
        first_failure_repair = (
            mode == "TDD"
            and bool(focus_ids)
            and all(int(report.get("iteration", -1)) == 0 for report in reports)
            and all(
                str(report.get("phase", "")) != "FRONTEND_BOOTSTRAP"
                for report in reports
            )
        )
        frontend_kinds = {"PAGE", "COMPONENT", "LAYOUT", "STORE"}
        focus_contains_frontend = any(
            str(bindings.get(module_id, {}).get("kind", "")) in frontend_kinds
            for module_id in focus_ids
        )
        if first_failure_repair and not focus_contains_frontend:
            relevant_writable = _one_hop_writable(focus_ids, writable_ids, bindings)
        if not relevant_writable:
            errors.append(
                f"ARC4530 IMPLEMENTATION_CONTEXT_INVALID: {requirement_id} has no writable targets."
            )
        relevant_read_only = read_only_ids & {
            str(value)
            for module_id in relevant_writable
            for value in bindings.get(module_id, {}).get("callees", [])
            if str(value)
        }
        relevant_read_only.update(
            module_id
            for module_id in read_only_ids
            if any(
                str(value) in relevant_writable
                for value in bindings.get(module_id, {}).get("callees", [])
            )
        )
        relevant_read_only.update(
            str(target.get("module_id", ""))
            for report in reports
            for target in report.get("read_only_dependencies", [])
            if isinstance(target, dict) and str(target.get("module_id", "")) in read_only_ids
        )
        relevant_read_only.update(
            _frontend_api_dependency_ids(
                request.design_context,
                screen_ids=relevant_writable,
                allowed_ids=read_only_ids,
            )
        )

        source_hashes: dict[str, str] = {}
        source_documents: dict[str, dict[str, Any]] = {}
        writable_cards: list[dict[str, Any]] = []
        for module_id in sorted(relevant_writable):
            binding = bindings.get(module_id)
            if binding is None or not bool(binding.get("editable")):
                errors.append(
                    f"ARC4530 IMPLEMENTATION_CONTEXT_INVALID: writable binding unavailable: {module_id}."
                )
                continue
            relative = _safe_workspace_file(str(binding.get("file", "")), source=True)
            if relative is None:
                errors.append(
                    f"ARC4530 IMPLEMENTATION_CONTEXT_INVALID: unsafe source path for {module_id}."
                )
                continue
            target = (self.output_root / Path(relative)).resolve()
            if self.output_root not in target.parents or not target.is_file():
                errors.append(
                    f"ARC4530 IMPLEMENTATION_CONTEXT_INVALID: source escapes workspace "
                    f"or does not exist: {relative}."
                )
                continue
            try:
                source = _read_text(target)
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
            except (OSError, UnicodeError) as exc:
                errors.append(
                    f"ARC4530 IMPLEMENTATION_CONTEXT_INVALID: cannot read {relative}: {exc}"
                )
                continue
            source_hashes[module_id] = digest
            source_documents.setdefault(
                relative,
                {"file": relative, "sha256": digest, "source": source},
            )
            writable_cards.append(_source_card(binding, digest=digest))

        read_only_cards = [
            _source_card(bindings[module_id], digest=None)
            for module_id in sorted(relevant_read_only)
            if module_id in bindings
        ]
        frozen_tests, test_errors = ([], [])
        if mode == "TDD":
            failed_layers = {
                str(report.get("layer", "")).upper()
                for report in reports
                if str(report.get("layer", "")).upper()
                in {"UNIT", "INTEGRATION", "E2E"}
            }
            frozen_tests, test_errors = self._frozen_tests(
                requirement_id,
                request.test_manifest,
                layers=failed_layers,
            )
        errors.extend(test_errors)
        if errors:
            return {}, {}, focus_ids, list(dict.fromkeys(errors))

        projected_design_context = _project_design_context(
            request.design_context,
            requirement_id=requirement_id,
            target_ids=relevant_writable | relevant_read_only,
        )
        projected_reports = _project_failure_reports(
            reports,
            target_ids=relevant_writable | relevant_read_only,
        )
        context = {
            "schema_version": IMPLEMENTATION_AGENT_SCHEMA_VERSION,
            "implementation_mode": mode,
            "requirement_id": requirement_id,
            "iteration": request.iteration,
            "requirement": request.requirement,
            "requirement_contract": request.requirement_contract,
            "design_context": projected_design_context,
            "failure_reports": projected_reports,
            "frozen_tests": frozen_tests,
            "allowed_writable_module_ids": sorted(source_hashes),
            "writable_targets": writable_cards,
            "writable_source_files": [
                source_documents[key] for key in sorted(source_documents)
            ],
            "read_only_dependencies": read_only_cards,
            "previous_patch_summary": request.previous_patch_summary,
            "policy": {
                "one_failure_cluster": True,
                "tests_are_frozen": mode == "TDD",
                "tests_limited_to_failed_layers": mode == "TDD",
                "writable_context_scope": (
                    "FAILURE_TARGET_PLUS_ONE_HOP"
                    if relevant_writable != writable_ids
                    else "ALL_REQUIREMENT_OWNED"
                ),
                "design_context_scope": "WRITABLE_TARGET_PLUS_ONE_HOP",
                "aggregate_mode_uses_design_and_binding_authority": mode == "AGGREGATE",
                "output_is_region_replacement_only": True,
                "side_effects_owned_by_orchestrator": True,
            },
            "project_conventions": {
                "frontend_styling": "Tailwind CSS v4 via @tailwindcss/vite",
                "frontend_css_entry": "frontend/src/index.css",
                "frontend_edits_use_static_utility_classes": True,
                "reference_images_are_visual_guidance_not_content_fixtures": True,
            },
        }
        context_size = len(
            json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        )
        if context_size > self._max_context_characters:
            section_sizes = {
                key: len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
                for key, value in context.items()
            }
            largest_sections = ", ".join(
                f"{key}={size}"
                for key, size in sorted(
                    section_sizes.items(), key=lambda item: item[1], reverse=True
                )[:6]
            )
            return (
                {},
                {},
                focus_ids,
                [
                    "ARC4532 IMPLEMENTATION_CONTEXT_TOO_LARGE: "
                    f"{context_size} characters exceeds {self._max_context_characters}; "
                    f"largest_sections: {largest_sections}."
                ],
            )
        return context, source_hashes, focus_ids, []

    def _frozen_tests(
        self,
        requirement_id: str,
        manifest: dict[str, Any],
        *,
        layers: set[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        tests: list[dict[str, Any]] = []
        errors: list[str] = []
        rows = [
            row
            for row in manifest.get("files", [])
            if isinstance(row, dict)
            and str(row.get("requirement_id", "")) == requirement_id
            and (
                not layers
                or str(row.get("layer", "")).upper() in layers
            )
        ]
        if not rows:
            return [], [
                "ARC4530 IMPLEMENTATION_CONTEXT_INVALID: no frozen tests for "
                f"{requirement_id} in failed layers {sorted(layers)}."
            ]
        for row in rows:
            relative = _safe_workspace_file(str(row.get("test_file", "")), source=False)
            if relative is None or row.get("status") != "FROZEN":
                errors.append(
                    f"ARC4530 IMPLEMENTATION_CONTEXT_INVALID: invalid frozen test row {row}."
                )
                continue
            target = (self.output_root / Path(relative)).resolve()
            if self.output_root not in target.parents or not target.is_file():
                errors.append(
                    f"ARC4530 IMPLEMENTATION_CONTEXT_INVALID: test escapes workspace "
                    f"or does not exist: {relative}."
                )
                continue
            try:
                raw = target.read_bytes()
                source = raw.decode("utf-8")
            except (OSError, UnicodeError) as exc:
                errors.append(
                    f"ARC4530 IMPLEMENTATION_CONTEXT_INVALID: cannot read {relative}: {exc}"
                )
                continue
            digest = hashlib.sha256(raw).hexdigest()
            if digest != str(row.get("content_sha256", "")):
                errors.append(
                    f"ARC4533 FROZEN_TEST_CHANGED: integrity mismatch for {relative}."
                )
                continue
            tests.append(
                {
                    "layer": str(row.get("layer", "")),
                    "test_file": relative,
                    "test_ids": [str(value) for value in row.get("test_ids", [])],
                    "source": source,
                }
            )
        return tests, errors


def _validate_decision(
    decision: dict[str, Any],
    *,
    allowed_ids: set[str],
    focus_ids: set[str],
) -> list[str]:
    if not isinstance(decision, dict) or set(decision) != {"summary", "edits"}:
        return ["ARC4534 IMPLEMENTATION_OUTPUT_INVALID: output must contain summary and edits."]
    summary = decision.get("summary")
    edits = decision.get("edits")
    errors: list[str] = []
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
        errors.append("ARC4534 IMPLEMENTATION_OUTPUT_INVALID: summary is invalid.")
    if not isinstance(edits, list) or not 1 <= len(edits) <= 16:
        return [*errors, "ARC4534 IMPLEMENTATION_OUTPUT_INVALID: edits must contain 1..16 rows."]
    actual_ids: list[str] = []
    for row in edits:
        if not isinstance(row, dict) or set(row) != {"module_id", "replacement"}:
            errors.append("ARC4534 IMPLEMENTATION_OUTPUT_INVALID: malformed edit row.")
            continue
        module_id = str(row.get("module_id", "")).strip()
        replacement = row.get("replacement")
        actual_ids.append(module_id)
        if module_id not in allowed_ids:
            errors.append(
                f"ARC4535 IMPLEMENTATION_TARGET_INVALID: {module_id!r} is not writable."
            )
        if not isinstance(replacement, str) or not replacement.strip():
            errors.append(
                f"ARC4534 IMPLEMENTATION_OUTPUT_INVALID: {module_id!r} has no replacement."
            )
        elif len(replacement.encode("utf-8")) > 200_000:
            errors.append(
                f"ARC4534 IMPLEMENTATION_OUTPUT_INVALID: {module_id!r} replacement is too large."
            )
        elif any(
            token in replacement
            for token in (
                "ARC-IMPLEMENTATION-BEGIN:",
                "ARC-IMPLEMENTATION-END:",
                "@arc-module",
                "```",
            )
        ):
            errors.append(
                f"ARC4534 IMPLEMENTATION_OUTPUT_INVALID: {module_id!r} contains forbidden framing."
            )
        elif re.search(r"(?m)^\s*(?:import|export)\s", replacement):
            errors.append(
                f"ARC4534 IMPLEMENTATION_OUTPUT_INVALID: {module_id!r} attempts to change "
                "a file-level import or export."
            )
    if len(actual_ids) != len(set(actual_ids)):
        errors.append("ARC4535 IMPLEMENTATION_TARGET_INVALID: duplicate module edits.")
    return list(dict.fromkeys(errors))


def _report_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    serializer = getattr(value, "to_dict", None)
    if callable(serializer):
        serialized = serializer()
        return serialized if isinstance(serialized, dict) else None
    return None


def _one_hop_writable(
    focus_ids: set[str],
    writable_ids: set[str],
    bindings: dict[str, dict[str, Any]],
) -> set[str]:
    """Return focused writable modules plus direct writable callers/callees."""

    result = set(focus_ids) & writable_ids
    for module_id in list(result):
        binding = bindings.get(module_id, {})
        result.update(
            str(value)
            for key in ("callers", "callees")
            for value in binding.get(key, [])
            if str(value) in writable_ids
        )
    result.update(
        candidate_id
        for candidate_id in writable_ids
        if any(
            str(value) in focus_ids
            for value in bindings.get(candidate_id, {}).get("callees", [])
        )
    )
    return result


def _project_design_context(
    design_context: dict[str, Any] | None,
    *,
    requirement_id: str,
    target_ids: set[str],
) -> dict[str, Any]:
    """Keep only Design evidence reachable from this implementation request."""

    if not isinstance(design_context, dict):
        return {}
    frontend = design_context.get("frontend", {})
    if not isinstance(frontend, dict):
        frontend = {}

    available_screens = [
        row for row in frontend.get("screens", []) if isinstance(row, dict)
    ]
    screens = [
        _compact_frontend_design_row(row, "screen")
        for row in available_screens
        if str(row.get("id", "")) in target_ids
    ]
    primary_screen_ids = {str(row.get("id", "")) for row in screens}
    route_index = {
        str(row.get("route", "")): row
        for row in available_screens
        if str(row.get("route", ""))
    }
    navigation_routes = {
        str(target.get("target_route", ""))
        for screen in screens
        for target in screen.get("navigation_targets", [])
        if isinstance(target, dict) and str(target.get("target_route", ""))
    }
    screen_ids = set(primary_screen_ids)
    for route in sorted(navigation_routes):
        destination = route_index.get(route)
        destination_id = str((destination or {}).get("id", ""))
        if destination is not None and destination_id not in screen_ids:
            screens.append(_compact_frontend_design_row(destination, "screen"))
            screen_ids.add(destination_id)

    journeys = [
        _compact_frontend_design_row(row, "journey")
        for row in frontend.get("journeys", [])
        if isinstance(row, dict)
        and str(row.get("source_screen_id", "")) in primary_screen_ids
    ]
    api_usages = [
        _compact_frontend_design_row(row, "api_usage")
        for row in frontend.get("api_usages", [])
        if isinstance(row, dict)
        and str(row.get("screen_id", "")) in primary_screen_ids
    ]
    shared_state_policies = [
        _compact_frontend_design_row(row, "state")
        for row in frontend.get("shared_state_policies", [])
        if isinstance(row, dict) and str(row.get("id", "")) in target_ids
    ]
    visual_ids = {
        str(value)
        for screen in screens
        for value in screen.get("visual_reference_ids", [])
        if str(value)
    }
    visual_references = [
        _compact_frontend_design_row(row, "visual")
        for row in frontend.get("visual_references", [])
        if isinstance(row, dict) and str(row.get("id", "")) in visual_ids
    ]

    active_link = design_context.get("active_requirement_link", {})
    projected_link: dict[str, Any] = {}
    if isinstance(active_link, dict) and (screens or shared_state_policies):
        projected_link = copy.deepcopy(active_link)
        if isinstance(projected_link.get("screen_ids"), list):
            projected_link["screen_ids"] = [
                str(item)
                for item in projected_link["screen_ids"]
                if str(item) in screen_ids
            ]
        if isinstance(projected_link.get("shared_state_ids"), list):
            retained_state_ids = {
                str(row.get("id", "")) for row in shared_state_policies
            }
            projected_link["shared_state_ids"] = [
                str(item)
                for item in projected_link["shared_state_ids"]
                if str(item) in retained_state_ids
            ]
        if isinstance(projected_link.get("visual_reference_ids"), list):
            projected_link["visual_reference_ids"] = [
                str(item)
                for item in projected_link["visual_reference_ids"]
                if str(item) in visual_ids
            ]

    return {
        "requirement_id": requirement_id,
        "module_ids": sorted(
            str(value)
            for value in design_context.get("module_ids", [])
            if str(value) in target_ids
        ),
        "backend_modules": [
            _compact_design_module(row)
            for row in design_context.get("backend_modules", [])
            if isinstance(row, dict)
            and str(row.get("id", row.get("module_id", ""))) in target_ids
        ],
        "frontend_scope": "IMPLEMENTATION_TARGET_ONE_HOP",
        "active_requirement_link": projected_link,
        "frontend": {
            "screens": screens,
            "journeys": journeys,
            "api_usages": api_usages,
            "shared_state_policies": shared_state_policies,
            "visual_references": visual_references,
        },
    }


def _compact_design_module(row: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "id", "module_id", "kind", "name", "description", "requirement_id",
        "file", "symbol", "public_signature", "inputs", "outputs", "effects",
        "callees", "callers", "route", "method", "request", "response",
        "obligations", "behavioral_obligations", "constraints",
    )
    return {key: copy.deepcopy(row.get(key)) for key in fields if key in row}


def _compact_frontend_design_row(row: dict[str, Any], kind: str) -> dict[str, Any]:
    fields = {
        "screen": ("id", "route", "title", "description", "requirement_ids", "required_api_ids", "navigation_targets", "visual_reference_ids"),
        "journey": ("id", "requirement_id", "source_screen_id", "target_screen_id", "steps", "api_id"),
        "api_usage": ("screen_id", "consumer_id", "api_id", "purpose", "trigger"),
        "state": ("id", "name", "requirement_ids", "persistence", "storage_key", "state", "actions"),
        "visual": ("id", "uri", "path", "description", "analysis", "style_summary"),
    }[kind]
    return {key: copy.deepcopy(row.get(key)) for key in fields if key in row}


def _frontend_api_dependency_ids(
    design_context: dict[str, Any] | None,
    *,
    screen_ids: set[str],
    allowed_ids: set[str],
) -> set[str]:
    if not isinstance(design_context, dict):
        return set()
    frontend = design_context.get("frontend", {})
    if not isinstance(frontend, dict):
        return set()
    api_ids = {
        str(row.get("api_id", ""))
        for row in frontend.get("api_usages", [])
        if isinstance(row, dict)
        and str(row.get("screen_id", "")) in screen_ids
        and str(row.get("api_id", ""))
    }
    api_ids.update(
        str(value)
        for row in frontend.get("screens", [])
        if isinstance(row, dict) and str(row.get("id", "")) in screen_ids
        for value in row.get("required_api_ids", [])
        if str(value)
    )
    candidates = api_ids | {f"API_CLIENT::{api_id}" for api_id in api_ids}
    return candidates & allowed_ids


def _project_failure_reports(
    reports: list[dict[str, Any]],
    *,
    target_ids: set[str],
) -> list[dict[str, Any]]:
    """Remove duplicated target cards after they have been used for localization."""

    projected: list[dict[str, Any]] = []
    for report in reports:
        row = {
            key: copy.deepcopy(value)
            for key, value in report.items()
            if key not in {"writable_targets", "read_only_dependencies"}
        }
        if isinstance(row.get("target_modules"), list):
            row["target_modules"] = [
                str(value)
                for value in row["target_modules"]
                if str(value) in target_ids
            ]
        row["writable_target_ids"] = sorted(
            {
                str(target.get("module_id", ""))
                for target in report.get("writable_targets", [])
                if isinstance(target, dict)
                and str(target.get("module_id", "")) in target_ids
            }
        )
        row["read_only_dependency_ids"] = sorted(
            {
                str(target.get("module_id", ""))
                for target in report.get("read_only_dependencies", [])
                if isinstance(target, dict)
                and str(target.get("module_id", "")) in target_ids
            }
        )
        projected.append(row)
    return projected


def _source_card(binding: dict[str, Any], *, digest: str | None) -> dict[str, Any]:
    card = {
        key: binding.get(key)
        for key in (
            "module_id",
            "kind",
            "file",
            "symbol",
            "public_signature",
            "input_type",
            "output_type",
            "props_type",
            "route",
            "callees",
            "editable",
            "implementation_region",
        )
    }
    if digest is not None:
        card["source_sha256"] = digest
    return card


def _safe_workspace_file(value: str, *, source: bool) -> str | None:
    normalized = str(value).replace("\\", "/").strip().strip("/")
    path = PurePosixPath(normalized)
    roots = ("backend/src/", "frontend/src/") if source else ("tests/",)
    suffixes = (".ts", ".tsx") if source else (".spec.ts",)
    if (
        not normalized
        or path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or not normalized.startswith(roots)
        or not normalized.endswith(suffixes)
    ):
        return None
    return normalized


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()
