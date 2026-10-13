from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from model.structured import BaseStructuredAgent, StructuredModel
from compiler.implementation.contracts import ProposedEdit, ProposedPatch
from compiler.frontend_semantics import REACT_SEMANTIC_EXAMPLES


IMPLEMENTATION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["edits"],
    "properties": {
        "edits": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["file", "search", "replacement"],
                "properties": {
                    "file": {"type": "string"},
                    "search": {"type": "string"},
                    "replacement": {"type": "string"},
                },
            },
        },
    },
}


IMPLEMENTATION_SCOPE_SCHEMA = {
    **IMPLEMENTATION_OUTPUT_SCHEMA,
    "properties": {
        "edits": {**IMPLEMENTATION_OUTPUT_SCHEMA["properties"]["edits"], "minItems": 0},
        "request_files": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["file", "reason"],
            "properties": {"file": {"type": "string"}, "reason": {"type": "string", "minLength": 1}}}},
    },
}


IMPLEMENTATION_INSTRUCTIONS = """Implement the supplied requirement in the editable source files.
Dependencies are read-only unless explicitly included in editable_files for this task.
File ownership and per-call write permission are different: shared types, callees, ancestor
components and integration files may be editable when needed to implement this requirement.
Use source_targets for actual positional parameters, return types, context types and component props.
Editable integration files may be changed to mount middleware or compose components;
preserve the declared API paths/methods and existing public behavior.
Preserve behavior of preserve_requirements in every shared file. Complete only the current
requirement and the supporting composition/call chain it needs, not every co-located feature.
If a required file is absent, return edits:[] and request_files:[{file,reason}] using file_catalog.
The compiler loads requested files, rebuilds editable_files and asks again. This is not an edit.
A file listed in the catalog is editable only after it appears in editable_files for this call.
Do not combine requests and edits. Finish with nonempty edits and omit request_files (or use []).
Preserve dependency public contracts and behavior used by other requirements.
The requirement is the source of behavior. Read the complete related source files for
existing signatures, imports, exports, types and dependencies.
This is the initial implementation stage. All supplied tests are read-only.
implementation_feedback is compiler guidance, not a test execution result.
Do not invent paths or edit read-only files. Preserve existing public
interfaces, routes and generated glue. Return only JSON with exact file/search/replacement
edits. Copy search verbatim from a unique fragment of the current editable file.
The keys of editable_files are the complete write allowlist for this invocation.
related_files are read-only context.
The supplied router and related source files define the HTTP method, path and input source: use query for query inputs
and body for body inputs in both handlers and test requests. Do not infer the method
from a function name or change generated routes to accommodate an incorrect test.
When a page, its ancestors and its owned components are editable, implement the required
composition and behavior together. Wire the current screen into the shell and entrypoint
using the declared page routes; preserve other screens and shared behavior.
"""

FRONTEND_IMPLEMENTATION_INSTRUCTIONS = IMPLEMENTATION_INSTRUCTIONS + """
Use Tailwind CSS utilities in JSX to implement the actual visual styling, not just
semantic markup. Tailwind does not generate CSS for invented semantic class names
such as field-row or brand-header. Use a custom class only if its definition exists
in a supplied stylesheet; otherwise replace it with complete Tailwind utility names.
Do not concatenate utility fragments dynamically; use complete literal alternatives.

When visual_references contain analyses, follow their layout_cues, style_cues,
regions and text_cues for composition, color, typography, spacing and controls.
Translate that evidence into concrete responsive Tailwind classes. Reuse the same
visual language across related components, preserving existing shared components.
When no reference analysis is supplied, choose a beautiful, minimal style suited to
the product: restrained colors, readable type, consistent spacing, clear hierarchy,
and whitespace. Follow existing application styling where it is already established.
Avoid unnecessary cards, gradients and shadows. Style forms, buttons and navigation,
including focus-visible, hover, disabled, loading and error states as applicable.
Keep the layout usable on mobile and desktop. The compiler's initial base stylesheet
is not a finished design. Before returning edits, check that all visible UI has real
styling and that every non-utility class you use has an existing CSS definition.
Passing behavioral tests alone does not establish visual completion.

Implement the current requirement's semantic UI region, nested handlers, positional frontend
functions and typed API clients. Multiple UI IDs can map to ONE component export and file;
do not create a component for each leaf node. Use component_id and the supplied frontend_subgraph.
Preserve unrelated controls and events sharing that component. Empty render bodies return null;
handler/function/API bodies throw NotImplementedError. Replace only the required placeholders.
Implement layout, navigation, accessible controls and applicable loading/error states.
Props and state/context interfaces are contracts, not a running state-management system.
Add local state, event payload extraction, effects and API transport where the requirement needs them.
Wire existing child components and ancestors without duplicating their markup. The generated
route plan defines navigation targets; do not invent routes or expect a generated UI runtime.
Only the keys of editable_files define the writable scope.
"""


FRONTEND_IMPLEMENTATION_INSTRUCTIONS += REACT_SEMANTIC_EXAMPLES


@dataclass(frozen=True, slots=True)
class ImplementationRequest:
    requirement_id: str
    requirement: dict[str, Any]
    code_binding_registry: dict[str, Any]
    target_module_ids: tuple[str, ...] = ()
    test_files: tuple[str, ...] = ()
    frontend_ir: dict[str, Any] | None = None
    iteration: int = 0
    implementation_feedback: str = ""


@dataclass(slots=True)
class ImplementationResult:
    requirement_id: str
    status: str
    patch: ProposedPatch | None = None
    attempts: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "PATCH_PROPOSED" and self.patch is not None and not self.errors


class ImplementationAgent:
    def __init__(
        self,
        model: StructuredModel,
        output_root: Path,
        *,
        retries: int = 2,
        max_context_characters: int = 600_000,
        trace: Callable[[str], None] | None = None,
        model_log: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.output_root = output_root.expanduser().resolve()
        self._max_context_characters = max_context_characters
        self._allowed_kinds: set[str] | None = {"DB", "FUNC", "API"}
        self._agent = BaseStructuredAgent(
            model,
            schema_name="arc_implementation_patch",
            instructions=IMPLEMENTATION_INSTRUCTIONS,
            output_schema=IMPLEMENTATION_SCOPE_SCHEMA,
            retries=retries,
            trace=trace,
            model_log=model_log,
            agent_name="ImplementationAgent",
        )
        self._trace = trace

    def implement(
        self,
        request: ImplementationRequest,
        *,
        accept_patch: Callable[[ProposedPatch], list[str]] | None = None,
    ) -> ImplementationResult:
        requirement_id = str(request.requirement_id).strip()
        requested_files: set[str] = set()
        attempts = 0
        for expansion in range(5):
            try:
                context, hashes = self._context(request, requested_files=requested_files)
            except (KeyError, ValueError, OSError, UnicodeError) as exc:
                return ImplementationResult(requirement_id, "CONTEXT_REJECTED", attempts=attempts, errors=[str(exc)])
            proposed_patch: ProposedPatch | None = None
            file_requests: list[dict[str, str]] = []

            def validate_attempt(output: dict[str, Any]) -> list[str]:
                nonlocal proposed_patch, file_requests
                if not isinstance(output, dict):
                    return ["Return a JSON object with edits or file requests"]
                requests = output.get("request_files", [])
                if not isinstance(requests, list):
                    return ["request_files must be an array."]
                if requests:
                    catalog = {row["file"] for row in context["file_catalog"]}
                    if output.get("edits") or set(output) != {"edits", "request_files"}:
                        return ["Return edits:[] with file requests; do not combine requests and edits."]
                    if any(not isinstance(row, dict) or set(row) != {"file", "reason"}
                           or not isinstance(row["file"], str) or row["file"] not in catalog
                           or not isinstance(row["reason"], str) or not row["reason"].strip() for row in requests):
                        return ["Request only application files in file_catalog and explain why each is needed."]
                    loaded = context["editable_files"].keys() | context["related_files"].keys()
                    if any(row["file"] in loaded for row in requests):
                        return ["Requested files are already loaded; return implementation edits."]
                    if expansion == 4:
                        return ["File context request limit exceeded (4 expansions); use loaded files."]
                    file_requests = requests
                    return []
                if set(output) - {"edits", "request_files"}:
                    return ["Return only edits or file requests."]
                errors = self._validate({"edits": output.get("edits")}, hashes)
                if errors:
                    return errors
                patch = ProposedPatch(requirement_id=requirement_id, edits=tuple(
                    ProposedEdit(file=row["file"], expected_sha256=hashes[row["file"]],
                                 search=row["search"], replacement=row["replacement"])
                    for row in output["edits"]))
                if accept_patch is not None:
                    errors = accept_patch(patch)
                    if errors:
                        return errors
                proposed_patch = patch
                return []

            invocation = self._agent.invoke(context, validate=validate_attempt)
            attempts += invocation.attempts
            if not invocation.ok or invocation.output is None:
                return ImplementationResult(requirement_id, "MODEL_REJECTED", attempts=attempts, errors=invocation.errors)
            if file_requests:
                requested_files.update(row["file"] for row in file_requests)
                if self._trace:
                    self._trace(f"FILE_SCOPE_EXPANDED requirement={requirement_id} requests={file_requests!r}")
                continue
            return ImplementationResult(requirement_id, "PATCH_PROPOSED", patch=proposed_patch, attempts=attempts)
        return ImplementationResult(requirement_id, "CONTEXT_REJECTED", attempts=attempts,
                                    errors=["File context request limit exceeded (4 expansions)."])

    def _context(self, request: ImplementationRequest, *, requested_files: set[str] | None = None) -> tuple[dict[str, Any], dict[str, str]]:
        from compiler.code_binding import CodeTargetResolver
        from compiler.implementation.file_context import file_selection_log
        from compiler.implementation.file_scope import RequirementFileScope

        resolved = CodeTargetResolver(request.code_binding_registry).resolve_requirement_targets(
            request.requirement_id
        )
        requested = set(request.target_module_ids)
        owned = [
            row for row in resolved["owned_targets"]
            if (not requested or row["module_id"] in requested)
            and (self._allowed_kinds is None or row["kind"] in self._allowed_kinds)
        ]
        if requested - {row["module_id"] for row in owned}:
            raise ValueError("Requested target is not writable by this requirement and agent.")
        mode = getattr(self, "_scope_mode", None) or ("frontend" if self._allowed_kinds and "UI" in self._allowed_kinds else "backend")
        if not owned and mode != "repair":
            raise ValueError("No writable source files for this requirement.")
        file_scope = RequirementFileScope(self.output_root, request.code_binding_registry, request.frontend_ir)
        scope = file_scope.select(request.requirement_id, mode=mode,
                                  target_ids={row["module_id"] for row in owned}, extra_files=requested_files, test_files=request.test_files)
        editable_paths = set(scope["editable"])
        related = set(scope["files"])
        paths = sorted(related)
        editable_files: dict[str, str] = {}
        related_files: dict[str, str] = {}
        hashes: dict[str, str] = {}
        for relative in paths:
            source = self._read_file(relative)
            if relative in editable_paths:
                editable_files[relative] = source
                hashes[relative] = hashlib.sha256(source.encode("utf-8")).hexdigest()
            else:
                related_files[relative] = source
        for relative in request.test_files:
            if relative not in paths:
                related_files[relative] = self._read_file(relative)
        if self._trace:
            self._trace(file_selection_log(request.requirement_id, "implementation", editable_files, "current_targets"))
            self._trace(file_selection_log(request.requirement_id, "implementation", related_files,
                                           "call_closure_types_composition_imports_support_and_failure_evidence"))
        from compiler.implementation.frontend_context import implementation_requirement
        requirement, missing_visuals = implementation_requirement(
            request.requirement, self.output_root, frontend=request.frontend_ir is not None)
        if missing_visuals and self._trace:
            self._trace(f"VISUAL_ANALYSIS_MISSING requirement={request.requirement_id} paths={missing_visuals!r}")
        context = {
            "requirement_id": request.requirement_id,
            "iteration": request.iteration,
            "implementation_mode": mode,
            "requirement": requirement,
            "editable_files": editable_files,
            "related_files": related_files,
            "source_targets": scope["targets"],
            "file_catalog": scope["file_catalog"],
            "file_scope": scope["reasons"],
            "preserve_requirements": scope["preserve_requirements"],
        }
        context.update(scope["design_context"])
        if request.implementation_feedback:
            context["implementation_feedback"] = request.implementation_feedback
        context_chars = len(str(context))
        if context_chars > self._max_context_characters:
            largest = sorted(((path, len(source)) for path, source in
                              {**related_files, **editable_files}.items()),
                             key=lambda item: (-item[1], item[0]))[:5]
            raise ValueError(
                f"Implementation context exceeds its size limit: {context_chars} > "
                f"{self._max_context_characters} characters; largest files={largest!r}.")
        return context, hashes

    def _read_file(self, relative: str) -> str:
        path = PurePosixPath(relative.replace(chr(92), "/"))
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("Unsafe source file path: " + relative)
        absolute = (self.output_root / Path(*path.parts)).resolve()
        if self.output_root not in absolute.parents or not absolute.is_file():
            raise ValueError("Source file missing or outside the workspace: " + relative)
        with absolute.open("r", encoding="utf-8", newline="") as handle:
            return handle.read()

    @staticmethod
    def _validate(output: dict[str, Any], hashes: dict[str, str]) -> list[str]:
        if not isinstance(output, dict) or set(output) != {"edits"}:
            return ["Return only an edits array."]
        edits = output["edits"]
        if not isinstance(edits, list) or not edits:
            return ["Return at least one exact edit."]
        errors: list[str] = []
        for row in edits:
            if not isinstance(row, dict) or set(row) != {"file", "search", "replacement"}:
                errors.append("Each edit needs file, search and replacement.")
            elif not isinstance(row["file"], str) or row["file"] not in hashes:
                errors.append(
                    f"Edit targets read-only or unknown file {row['file']!r}. "
                    "Regenerate edits using only the keys of editable_files; "
                    "related_files are context, not editable targets."
                )
            elif not isinstance(row["search"], str) or not row["search"]:
                errors.append("Search must contain an exact source fragment.")
            elif not isinstance(row["replacement"], str):
                errors.append("Replacement must be text.")
        return errors


class FrontendImplementationAgent(ImplementationAgent):
    def __init__(
        self,
        model: StructuredModel,
        output_root: Path,
        *,
        retries: int = 2,
        max_context_characters: int = 600_000,
        trace: Callable[[str], None] | None = None,
        model_log: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(
            model, output_root, retries=retries,
            max_context_characters=max_context_characters, trace=trace,
            model_log=model_log,
        )
        self._allowed_kinds = {"API_CLIENT", "UI", "UI_EVENT", "UI_FUNC"}
        self._agent = BaseStructuredAgent(
            model,
            schema_name="arc_frontend_implementation_patch",
            instructions=FRONTEND_IMPLEMENTATION_INSTRUCTIONS,
            output_schema=IMPLEMENTATION_SCOPE_SCHEMA,
            retries=retries,
            trace=trace,
            model_log=model_log,
            agent_name="FrontendImplementationAgent",
        )
