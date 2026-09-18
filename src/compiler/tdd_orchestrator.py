from __future__ import annotations

import copy
import hashlib
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from arc_agents import ImplementationAgent, ImplementationRequest, JsonModel
from arcbench_agent_runtime.jsonio import write_json_atomic

from .artifacts import CompilerArtifactStore
from .code_binding import CodeTargetResolver
from .failure_analysis import FailureAnalysisResult, FailureAnalyzer, TestFailureReport
from .test_generation import RequirementTestGenerationPass, TEST_LAYERS
from .test_runner import TestRunResult, TestRunner, TestSelection
from .write_guard import WriteGuard


NODE_TDD_SCHEMA_VERSION = 1
ACTIONABLE_FAILURE_CLASSES = (
    "TYPE_CONTRACT",
    "IMPLEMENTATION_BEHAVIOR",
    "VISUAL_BEHAVIOR",
)
TERMINAL_NODE_STATES = {
    "NODE_ACCEPTED",
    "BLOCKED_DEPENDENCY",
    "BLOCKED_INFRA",
    "BLOCKED_TEST_MATERIALIZATION",
    "BLOCKED_CONTRACT",
    "RED_NOT_OBSERVED",
    "AGENT_FAILED",
    "PATCH_REJECTED",
    "NO_PROGRESS",
    "ITERATION_BUDGET_EXHAUSTED",
    "REGRESSION_FAILED",
    "INTERNAL_ERROR",
}


@dataclass(frozen=True, slots=True)
class NodeTDDPolicy:
    """Budgets for one non-resumable requirement-local TDD run."""

    max_iterations_per_node: int = 8
    no_progress_limit: int = 2
    infra_retry_count: int = 2
    visual_refinement_limit: int = 2

    def __post_init__(self) -> None:
        for name in (
            "max_iterations_per_node",
            "no_progress_limit",
            "visual_refinement_limit",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1.")
        if self.infra_retry_count < 0:
            raise ValueError("infra_retry_count must not be negative.")

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "NodeTDDPolicy":
        values = os.environ if environment is None else environment
        return cls(
            max_iterations_per_node=_bounded_int(
                values, "ARC_TDD_MAX_ITERATIONS_PER_NODE", 8, 1, 50
            ),
            no_progress_limit=_bounded_int(
                values, "ARC_TDD_NO_PROGRESS_LIMIT", 2, 1, 10
            ),
            infra_retry_count=_bounded_int(
                values, "ARC_TDD_INFRA_RETRY_COUNT", 2, 0, 10
            ),
            visual_refinement_limit=_bounded_int(
                values, "ARC_TDD_VISUAL_REFINEMENT_LIMIT", 2, 1, 20
            ),
        )


@dataclass(slots=True)
class NodeTDDResult:
    requirement_id: str
    status: str
    iterations: int = 0
    visual_iterations: int = 0
    infrastructure_retries: int = 0
    state_history: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    impacted_requirements: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    schema_version: int = NODE_TDD_SCHEMA_VERSION

    @property
    def ok(self) -> bool:
        return self.status == "NODE_ACCEPTED" and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _RunAnalysis:
    test_run: TestRunResult
    analysis: FailureAnalysisResult
    infrastructure_retries: int


class NodeTDDOrchestrator:
    """Own the deterministic RED-to-GREEN lifecycle for exactly one node.

    The implementation model can only propose marker-scoped edits. This class
    owns test execution, failure routing, write authorization, budgets, state,
    regression checks, and artifacts. It intentionally does not restore prior
    TDD state from disk; dependency acceptance is scoped to this instance.
    """

    def __init__(
        self,
        model: JsonModel,
        output_root: Path,
        *,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any],
        database_schema: dict[str, Any],
        design_ir: dict[str, Any],
        frontend_ir: dict[str, Any],
        code_binding_registry: dict[str, Any],
        environment_manifest: dict[str, Any],
        artifact_store: CompilerArtifactStore | None = None,
        test_manifest: dict[str, Any] | None = None,
        policy: NodeTDDPolicy | None = None,
        test_generation: RequirementTestGenerationPass | None = None,
        test_runner: TestRunner | None = None,
        failure_analyzer: FailureAnalyzer | None = None,
        implementation_agent: ImplementationAgent | None = None,
        write_guard: WriteGuard | None = None,
    ) -> None:
        self.output_root = output_root.expanduser().resolve()
        self.requirement_ir = copy.deepcopy(requirement_ir)
        self.dependency_graph = copy.deepcopy(dependency_graph)
        self.database_schema = copy.deepcopy(database_schema)
        self.design_ir = copy.deepcopy(design_ir)
        self.frontend_ir = copy.deepcopy(frontend_ir)
        self.code_binding_registry = copy.deepcopy(code_binding_registry)
        self.environment_manifest = copy.deepcopy(environment_manifest)
        self.test_manifest = copy.deepcopy(test_manifest) if test_manifest else None
        self.policy = policy or NodeTDDPolicy.from_environment()
        self.artifact_store = artifact_store or CompilerArtifactStore(self.output_root)

        self.test_generation = test_generation or RequirementTestGenerationPass(
            model,
            self.output_root,
            self.artifact_store,
        )
        self.test_runner = test_runner or TestRunner(self.output_root)
        self.failure_analyzer = failure_analyzer or FailureAnalyzer(self.output_root)
        self.implementation_agent = implementation_agent or ImplementationAgent(
            model,
            self.output_root,
        )
        self.write_guard = write_guard or WriteGuard(self.output_root)

        self.node_states: dict[str, str] = {}
        self._state_history: dict[str, list[str]] = {}
        self._accepted_results: dict[str, NodeTDDResult] = {}
        self._tdd_root = self.output_root / ".arc" / "tdd"

    def run_node(self, requirement_id: str) -> NodeTDDResult:
        """Generate this node's tests and drive only this node to acceptance."""

        requirement_id = str(requirement_id).strip()
        result = NodeTDDResult(requirement_id=requirement_id, status="INTERNAL_ERROR")
        try:
            if self.node_states.get(requirement_id) == "NODE_ACCEPTED":
                return copy.deepcopy(self._accepted_results[requirement_id])
            error = self._precondition_error(requirement_id)
            if error:
                state = (
                    "BLOCKED_DEPENDENCY"
                    if error.startswith("ARC4542")
                    else "INTERNAL_ERROR"
                )
                return self._finish(result, state, [error])

            self._transition(requirement_id, "NODE_DISCOVERED")
            generation = self.test_generation.generate_requirement(
                requirement_id=requirement_id,
                requirement_ir=self.requirement_ir,
                database_schema=self.database_schema,
                design_ir=self.design_ir,
                frontend_ir=self.frontend_ir,
                code_binding_registry=self.code_binding_registry,
                environment_manifest=self.environment_manifest,
                existing_manifest=self.test_manifest,
            )
            result.artifacts.update(generation.artifacts)
            if not generation.ok:
                return self._finish(
                    result,
                    "BLOCKED_TEST_MATERIALIZATION",
                    generation.errors,
                )
            self.test_manifest = generation.manifest
            self._transition(requirement_id, "TESTS_GENERATED")
            self._transition(requirement_id, "TESTS_FROZEN")

            baseline = self._run_and_analyze(
                requirement_id,
                iteration=0,
                include_typecheck=False,
                changed_files=[],
            )
            result.infrastructure_retries += baseline.infrastructure_retries
            if baseline.analysis.errors:
                return self._finish(result, "INTERNAL_ERROR", baseline.analysis.errors)
            if baseline.test_run.ok:
                return self._finish(
                    result,
                    "RED_NOT_OBSERVED",
                    [
                        "ARC4543 RED_NOT_OBSERVED: frozen tests already pass before "
                        f"implementing {requirement_id}."
                    ],
                )
            blocked = _blocked_state(baseline.analysis.reports)
            if blocked:
                return self._finish(
                    result,
                    blocked,
                    _report_messages(baseline.analysis.reports),
                )

            self._transition(requirement_id, "RED_CONFIRMED")
            reports = baseline.analysis.reports
            last_fingerprint = _selected_cluster(reports)[0].failure_fingerprint
            unchanged_failures = 0
            functional_iterations = 0
            visual_iterations = 0
            patch_iteration = 0
            changed_files: set[str] = set()
            previous_patch_summary: dict[str, Any] | None = None

            while True:
                cluster = _selected_cluster(reports)
                failure_class = cluster[0].failure_class
                if failure_class == "VISUAL_BEHAVIOR":
                    if visual_iterations >= self.policy.visual_refinement_limit:
                        return self._budget_exhausted(
                            result,
                            functional_iterations,
                            visual_iterations,
                            changed_files,
                            "visual refinement",
                        )
                    visual_iterations += 1
                else:
                    if functional_iterations >= self.policy.max_iterations_per_node:
                        return self._budget_exhausted(
                            result,
                            functional_iterations,
                            visual_iterations,
                            changed_files,
                            "implementation",
                        )
                    functional_iterations += 1
                patch_iteration += 1
                result.iterations = functional_iterations
                result.visual_iterations = visual_iterations
                self._transition(requirement_id, "IMPLEMENTING")

                implementation = self.implementation_agent.implement(
                    ImplementationRequest(
                        requirement_id=requirement_id,
                        requirement=self.requirement_ir["nodes"][requirement_id],
                        requirement_contract=self._requirement_contract(requirement_id),
                        test_manifest=self.test_manifest or {},
                        code_binding_registry=self.code_binding_registry,
                        failure_reports=tuple(cluster),
                        iteration=patch_iteration,
                        design_context=self._design_context(requirement_id),
                        previous_patch_summary=previous_patch_summary,
                    )
                )
                if not implementation.ok or implementation.patch is None:
                    return self._finish(
                        result,
                        "AGENT_FAILED",
                        implementation.errors
                        or [f"ARC4544 IMPLEMENTATION_AGENT_FAILED: {implementation.status}."],
                        iterations=functional_iterations,
                        visual_iterations=visual_iterations,
                        changed_files=changed_files,
                    )

                applied = self.write_guard.apply(
                    implementation.patch,
                    code_binding_registry=self.code_binding_registry,
                )
                if not applied.ok:
                    return self._finish(
                        result,
                        "PATCH_REJECTED",
                        applied.rejected_changes,
                        iterations=functional_iterations,
                        visual_iterations=visual_iterations,
                        changed_files=changed_files,
                    )
                changed_files.update(_normalize_path(value) for value in applied.changed_files)
                result.changed_files = sorted(changed_files)
                previous_patch_summary = {
                    "iteration": patch_iteration,
                    "summary": implementation.summary,
                    "changed_files": applied.changed_files,
                    "changed_modules": applied.changed_modules,
                }

                verification = self._run_and_analyze(
                    requirement_id,
                    iteration=patch_iteration,
                    include_typecheck=True,
                    changed_files=sorted(changed_files),
                )
                result.infrastructure_retries += verification.infrastructure_retries
                if verification.analysis.errors:
                    return self._finish(
                        result,
                        "INTERNAL_ERROR",
                        verification.analysis.errors,
                        iterations=functional_iterations,
                        visual_iterations=visual_iterations,
                        changed_files=changed_files,
                    )
                if verification.test_run.ok:
                    result.iterations = functional_iterations
                    result.visual_iterations = visual_iterations
                    result.changed_files = sorted(changed_files)
                    return self._accept_node(result)

                blocked = _blocked_state(verification.analysis.reports)
                if blocked:
                    return self._finish(
                        result,
                        blocked,
                        _report_messages(verification.analysis.reports),
                        iterations=functional_iterations,
                        visual_iterations=visual_iterations,
                        changed_files=changed_files,
                    )
                reports = verification.analysis.reports
                fingerprint = _selected_cluster(reports)[0].failure_fingerprint
                if fingerprint == last_fingerprint:
                    unchanged_failures += 1
                else:
                    unchanged_failures = 0
                last_fingerprint = fingerprint
                if unchanged_failures >= self.policy.no_progress_limit:
                    return self._finish(
                        result,
                        "NO_PROGRESS",
                        [
                            "ARC4545 NO_PROGRESS: the selected failure fingerprint remained "
                            f"unchanged for {unchanged_failures} implementation iterations."
                        ],
                        iterations=functional_iterations,
                        visual_iterations=visual_iterations,
                        changed_files=changed_files,
                    )
        except Exception as exc:
            return self._finish(
                result,
                "INTERNAL_ERROR",
                [f"ARC4540 NODE_TDD_INTERNAL_ERROR: {type(exc).__name__}: {exc}"],
                iterations=result.iterations,
                visual_iterations=result.visual_iterations,
                changed_files=result.changed_files,
            )

    def _run_and_analyze(
        self,
        requirement_id: str,
        *,
        iteration: int,
        include_typecheck: bool,
        changed_files: list[str],
    ) -> _RunAnalysis:
        retries = 0
        while True:
            test_run = self.test_runner.run(
                TestSelection(
                    requirement_id=requirement_id,
                    include_typecheck=include_typecheck,
                    stop_on_failure=True,
                ),
                test_manifest=self.test_manifest,
                environment_manifest=self.environment_manifest,
            )
            analysis = self.failure_analyzer.classify(
                test_run,
                code_binding_registry=self.code_binding_registry,
                test_manifest=self.test_manifest or {},
                iteration=iteration,
                changed_files=changed_files,
            )
            has_infrastructure_failure = any(
                report.failure_class == "INFRASTRUCTURE"
                for report in analysis.reports
            )
            if (
                test_run.ok
                or analysis.errors
                or not has_infrastructure_failure
                or retries >= self.policy.infra_retry_count
            ):
                return _RunAnalysis(test_run, analysis, retries)
            retries += 1

    def _accept_node(self, result: NodeTDDResult) -> NodeTDDResult:
        requirement_id = result.requirement_id
        available_layers = {
            str(row.get("layer", "")).upper()
            for row in (self.test_manifest or {}).get("files", [])
            if isinstance(row, dict)
            and str(row.get("requirement_id", "")) == requirement_id
        }
        for layer in TEST_LAYERS:
            if layer in available_layers:
                self._transition(requirement_id, f"{layer}_GREEN")

        impacted = self._impacted_accepted_requirements(
            requirement_id,
            set(result.changed_files),
        )
        result.impacted_requirements = impacted
        for impacted_id in impacted:
            test_run = self.test_runner.run(
                TestSelection(
                    requirement_id=impacted_id,
                    include_typecheck=False,
                    stop_on_failure=True,
                ),
                test_manifest=self.test_manifest,
                environment_manifest=self.environment_manifest,
            )
            if not test_run.ok:
                return self._finish(
                    result,
                    "REGRESSION_FAILED",
                    test_run.errors
                    or [f"ARC4546 REGRESSION_FAILED: accepted node {impacted_id} failed."],
                )

        self._transition(requirement_id, "NODE_ACCEPTED")
        return self._finish(result, "NODE_ACCEPTED", [])

    def _precondition_error(self, requirement_id: str) -> str | None:
        nodes = self.requirement_ir.get("nodes")
        if not requirement_id:
            return "ARC4541 NODE_TDD_INPUT_INVALID: requirement_id is required."
        if not isinstance(nodes, dict) or requirement_id not in nodes:
            return f"ARC4541 NODE_TDD_INPUT_INVALID: unknown requirement {requirement_id}."
        dependencies = self.dependency_graph.get("atomic_dependencies", {}).get(
            requirement_id, []
        )
        unaccepted = [
            str(value)
            for value in dependencies
            if self.node_states.get(str(value)) != "NODE_ACCEPTED"
        ]
        if unaccepted:
            return (
                f"ARC4542 NODE_TDD_DEPENDENCY_BLOCKED: {requirement_id} requires "
                f"accepted nodes {sorted(unaccepted)}."
            )
        return None

    def _requirement_contract(self, requirement_id: str) -> dict[str, Any]:
        return next(
            (
                copy.deepcopy(row.get("contract", {}))
                for row in self.design_ir.get("requirements", [])
                if isinstance(row, dict) and str(row.get("id", "")) == requirement_id
            ),
            {},
        )

    def _design_context(self, requirement_id: str) -> dict[str, Any]:
        resolved = CodeTargetResolver(
            self.code_binding_registry
        ).resolve_requirement_targets(requirement_id)
        module_ids = {
            str(row.get("module_id", ""))
            for key in ("owned_targets", "dependency_targets")
            for row in resolved.get(key, [])
            if isinstance(row, dict) and str(row.get("module_id", ""))
        }
        frontend_link = next(
            (
                copy.deepcopy(row)
                for row in self.frontend_ir.get("requirement_links", [])
                if isinstance(row, dict)
                and str(row.get("requirement_id", "")) == requirement_id
            ),
            {},
        )
        frontend_ids = {
            str(value) for value in frontend_link.get("symbol_ids", []) if str(value)
        } | module_ids
        frontend_rows: dict[str, list[dict[str, Any]]] = {}
        visual_ids = {
            str(value)
            for value in frontend_link.get("visual_reference_ids", [])
            if str(value)
        }
        for table in ("layouts", "pages", "components", "stores"):
            rows = [
                copy.deepcopy(row)
                for row in self.frontend_ir.get(table, [])
                if isinstance(row, dict)
                and (
                    str(row.get("id", "")) in frontend_ids
                    or str(row.get("requirement_id", "")) == requirement_id
                )
            ]
            frontend_rows[table] = rows
            visual_ids.update(
                str(value)
                for row in rows
                for value in row.get("visual_reference_ids", [])
                if str(value)
            )
        frontend_rows["api_dependencies"] = [
            copy.deepcopy(row)
            for row in self.frontend_ir.get("api_dependencies", [])
            if isinstance(row, dict)
            and (
                str(row.get("consumer_id", "")) in frontend_ids
                or str(row.get("api_id", "")) in module_ids
            )
        ]
        frontend_rows["visual_references"] = [
            copy.deepcopy(row)
            for row in self.frontend_ir.get("visual_references", [])
            if isinstance(row, dict) and str(row.get("id", "")) in visual_ids
        ]
        backend_modules = [
            copy.deepcopy(row)
            for row in self.design_ir.get("modules", [])
            if isinstance(row, dict)
            and (
                str(row.get("id", "")) in module_ids
                or str(row.get("module_id", "")) in module_ids
                or str(row.get("requirement_id", "")) == requirement_id
            )
        ]
        return {
            "requirement_id": requirement_id,
            "module_ids": sorted(module_ids),
            "backend_modules": backend_modules,
            "frontend": frontend_rows,
        }

    def _impacted_accepted_requirements(
        self,
        current_id: str,
        changed_files: set[str],
    ) -> list[str]:
        if not changed_files:
            return []
        impacted: set[str] = set()
        for row in (self.test_manifest or {}).get("tests", []):
            if not isinstance(row, dict):
                continue
            requirement_id = str(row.get("requirement_id", ""))
            if (
                requirement_id
                and requirement_id != current_id
                and self.node_states.get(requirement_id) == "NODE_ACCEPTED"
                and changed_files
                & {_normalize_path(value) for value in row.get("target_files", [])}
            ):
                impacted.add(requirement_id)
        return sorted(impacted)

    def _budget_exhausted(
        self,
        result: NodeTDDResult,
        iterations: int,
        visual_iterations: int,
        changed_files: set[str],
        budget_name: str,
    ) -> NodeTDDResult:
        return self._finish(
            result,
            "ITERATION_BUDGET_EXHAUSTED",
            [f"ARC4547 ITERATION_BUDGET_EXHAUSTED: {budget_name} budget was exhausted."],
            iterations=iterations,
            visual_iterations=visual_iterations,
            changed_files=changed_files,
        )

    def _transition(self, requirement_id: str, state: str) -> None:
        self.node_states[requirement_id] = state
        history = self._state_history.setdefault(requirement_id, [])
        if not history or history[-1] != state:
            history.append(state)

    def _finish(
        self,
        result: NodeTDDResult,
        status: str,
        errors: list[str],
        *,
        iterations: int | None = None,
        visual_iterations: int | None = None,
        changed_files: set[str] | list[str] | None = None,
    ) -> NodeTDDResult:
        if status not in TERMINAL_NODE_STATES:
            status = "INTERNAL_ERROR"
            errors = [*errors, "ARC4540 NODE_TDD_INTERNAL_ERROR: invalid terminal state."]
        self._transition(result.requirement_id or "unknown", status)
        result.status = status
        if iterations is not None:
            result.iterations = iterations
        if visual_iterations is not None:
            result.visual_iterations = visual_iterations
        if changed_files is not None:
            result.changed_files = sorted({_normalize_path(value) for value in changed_files})
        result.state_history = list(
            self._state_history.get(result.requirement_id or "unknown", [])
        )
        result.errors = list(dict.fromkeys(str(value) for value in errors if str(value)))
        result_path = self._node_root(result.requirement_id or "unknown") / "result.json"
        write_json_atomic(result_path, result.to_dict())
        result.artifacts["result"] = str(result_path)
        if status == "NODE_ACCEPTED":
            self._accepted_results[result.requirement_id] = copy.deepcopy(result)
        return result

    def _node_root(self, requirement_id: str) -> Path:
        safe = re.sub(r"[^a-z0-9]+", "-", requirement_id.lower()).strip("-")
        safe = safe or "requirement"
        digest = hashlib.sha256(requirement_id.encode("utf-8")).hexdigest()[:8]
        return self._tdd_root / f"{safe}-{digest}"


def _selected_cluster(reports: list[TestFailureReport]) -> list[TestFailureReport]:
    for failure_class in ACTIONABLE_FAILURE_CLASSES:
        candidates = [row for row in reports if row.failure_class == failure_class]
        if candidates:
            fingerprint = candidates[0].failure_fingerprint
            return [row for row in candidates if row.failure_fingerprint == fingerprint]
    raise ValueError("No actionable failure cluster is available.")


def _blocked_state(reports: list[TestFailureReport]) -> str | None:
    classes = {row.failure_class for row in reports}
    if "INFRASTRUCTURE" in classes:
        return "BLOCKED_INFRA"
    if "TEST_MATERIALIZATION" in classes:
        return "BLOCKED_TEST_MATERIALIZATION"
    if "TEST_OR_CONTRACT_INCONSISTENT" in classes:
        return "BLOCKED_CONTRACT"
    if not classes or not classes.issubset(set(ACTIONABLE_FAILURE_CLASSES)):
        return "BLOCKED_CONTRACT"
    return None


def _report_messages(reports: list[TestFailureReport]) -> list[str]:
    return list(dict.fromkeys(row.message for row in reports if row.message))


def _normalize_path(value: Any) -> str:
    return str(value).replace("\\", "/").strip().strip("/")


def _bounded_int(
    environment: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        return max(minimum, min(int(environment.get(name, str(default))), maximum))
    except (TypeError, ValueError):
        return default
