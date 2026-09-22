from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .code_binding import CODE_BINDING_READY, CodeTargetResolver
from .test_generation import TESTS_FROZEN
from .model_client import StructuredModel, describe_model_error
from .test_runner import TestCommandResult, TestRunResult


FAILURE_ANALYSIS_SCHEMA_VERSION = 2
FAILURE_CLASSES = (
    "INFRASTRUCTURE",
    "TEST_MATERIALIZATION",
    "TYPE_CONTRACT",
    "IMPLEMENTATION_BEHAVIOR",
    "VISUAL_BEHAVIOR",
    "TEST_OR_CONTRACT_INCONSISTENT",
    "DEFERRED_DEPENDENCY",
)


FAILURE_SYNTHESIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["analysis"],
    "properties": {
        "analysis": {
            "type": "string",
            "minLength": 1,
        }
    },
}


FAILURE_SYNTHESIS_INSTRUCTIONS = """You are ARC's test-failure analyst.
Read the failed-test sections extracted from Playwright's JSON reporter and pw:api output. Produce a concise but
information-preserving plain-text analysis for an implementation agent. For every FAILED TEST section, identify it by
test_file and title, then state the exact URL/route, locator or assertion,
expected value, received value, browser/runtime error, and the most relevant source location whenever those facts are
present. Separate observed facts from hypotheses. Do not invent missing facts, do not propose code, and do not omit a
useful error detail merely because it is repetitive. The failed-test sections remain attached after your analysis.
Return exactly one JSON object with one non-empty `analysis` string and no prose outside the JSON object.
"""


@dataclass(frozen=True, slots=True)
class StackFrame:
    file: str
    line: int | None = None
    column: int | None = None
    symbol: str | None = None


@dataclass(slots=True)
class TestFailureReport:
    requirement_id: str
    iteration: int
    test_id: str | None
    test_ids: list[str]
    layer: str | None
    phase: str
    failure_class: str
    message: str
    stack_frames: list[StackFrame]
    target_modules: list[str]
    writable_targets: list[dict[str, Any]]
    read_only_dependencies: list[dict[str, Any]]
    changed_files: list[str]
    failure_fingerprint: str
    command: list[str] = field(default_factory=list)
    diagnostic_output: str = ""
    deferred_dependency_modules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FailureAnalysisResult:
    requirement_id: str
    reports: list[TestFailureReport]
    errors: list[str] = field(default_factory=list)
    agent_context: str = ""
    schema_version: int = FAILURE_ANALYSIS_SCHEMA_VERSION

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FailureAnalyzer:
    """Convert runner output into requirement- and binding-aware failure reports."""

    def __init__(self, output_root: Path, model: StructuredModel | None = None) -> None:
        self.output_root = output_root.expanduser().resolve()
        self._model = model

    def classify(
        self,
        test_run: TestRunResult,
        *,
        code_binding_registry: dict[str, Any],
        test_manifest: dict[str, Any],
        iteration: int,
        changed_files: list[str] | None = None,
    ) -> FailureAnalysisResult:
        requirement_id = str(test_run.requirement_id).strip()
        errors = self._validate_inputs(
            requirement_id=requirement_id,
            code_binding_registry=code_binding_registry,
            test_manifest=test_manifest,
            iteration=iteration,
        )
        if errors:
            return FailureAnalysisResult(
                requirement_id=requirement_id,
                reports=[],
                errors=errors,
            )
        if test_run.ok:
            return FailureAnalysisResult(requirement_id=requirement_id, reports=[])

        resolver = CodeTargetResolver(code_binding_registry)
        resolved = resolver.resolve_requirement_targets(requirement_id)
        binding_by_id = {
            str(row.get("module_id", "")): row
            for row in code_binding_registry.get("code_bindings", [])
            if isinstance(row, dict) and str(row.get("module_id", ""))
        }
        modules_by_file = {
            _normalize_relative(str(row.get("file", ""))): [
                str(value) for value in row.get("module_ids", []) if str(value)
            ]
            for row in code_binding_registry.get("file_index", [])
            if isinstance(row, dict) and str(row.get("file", ""))
        }
        test_rows = [
            row
            for row in test_manifest.get("tests", [])
            if isinstance(row, dict)
            and str(row.get("requirement_id", "")) == requirement_id
        ]
        # Every module this requirement may edit. A stub hit outside that set is a
        # dependency the schedule has not built yet, not a defect in this node.
        owned_module_ids = {
            str(value)
            for key in ("owned", "writable")
            for value in resolved.get(key, [])
            if str(value)
        }
        reports: list[TestFailureReport] = []
        failed_commands = [row for row in test_run.commands if row.status != "PASSED"]
        for command_result in failed_commands:
            reports.append(
                self._report_for_command(
                    requirement_id=requirement_id,
                    iteration=iteration,
                    command_result=command_result,
                    test_rows=test_rows,
                    resolved_targets=resolved,
                    binding_by_id=binding_by_id,
                    modules_by_file=modules_by_file,
                    changed_files=changed_files or [],
                    owned_module_ids=owned_module_ids,
                )
            )

        if not reports:
            reports.append(
                self._report_for_runner_failure(
                    requirement_id=requirement_id,
                    iteration=iteration,
                    test_run=test_run,
                    resolved_targets=resolved,
                    binding_by_id=binding_by_id,
                    changed_files=changed_files or [],
                )
            )
        return FailureAnalysisResult(
            requirement_id=requirement_id,
            reports=reports,
            agent_context=self._agent_context(
                test_run,
                reports,
                test_rows=test_rows,
                binding_by_id=binding_by_id,
            ),
        )

    def _agent_context(
        self,
        test_run: TestRunResult,
        reports: list[TestFailureReport],
        *,
        test_rows: list[dict[str, Any]],
        binding_by_id: dict[str, dict[str, Any]],
    ) -> str:
        """Turn failed Playwright tests into one readable repair context.

        Routing still uses the deterministic reports above. The implementation
        model receives only failed-test sections instead of nested report objects.
        """

        e2e_commands = [
            command
            for command in test_run.commands
            if str(command.layer or "").upper() == "E2E"
            and command.status != "PASSED"
        ]
        if not e2e_commands:
            return "\n\n".join(
                "FAILURE ANALYSIS\n"
                f"failure_class={report.failure_class}\n"
                f"phase={report.phase}\n"
                f"message={report.message}\n"
                f"target_modules={', '.join(report.target_modules) or '(not localized)'}\n"
                f"details={report.diagnostic_output or '(none)'}"
                for report in reports
            )
        raw_parts: list[str] = []
        pw_api_parts: list[str] = []
        for command in e2e_commands:
            raw_stdout = command.raw_stdout or command.stdout
            raw_stderr = command.raw_stderr or command.stderr
            if raw_stdout:
                raw_parts.append(raw_stdout)
            if raw_stderr:
                raw_parts.append(raw_stderr)
                pw_api_parts.extend(
                    line for line in raw_stderr.splitlines() if "pw:api" in line
                )
        raw_report = "\n".join(raw_parts).strip()
        failed_tests = _playwright_failed_tests(raw_report)
        failed_sections = self._failed_test_sections(
            failed_tests=failed_tests,
            reports=reports,
            test_rows=test_rows,
            binding_by_id=binding_by_id,
            pw_api_lines=pw_api_parts,
        )
        failure_log = "\n\n".join(failed_sections).strip()
        if not failure_log:
            failure_log = _fallback_e2e_failure_log(
                reports=reports,
                commands=e2e_commands,
                raw_report=raw_report,
            )
        routing = "\n".join(
            " | ".join(
                [
                    f"failure_class={report.failure_class}",
                    f"phase={report.phase}",
                    f"target_modules={', '.join(report.target_modules) or '(not localized)'}",
                    f"test_ids={', '.join(report.test_ids) or '(not matched)'}",
                ]
            )
            for report in reports
        )
        synthesis = ""
        if self._model is not None:
            try:
                decision = self._model.generate_json(
                    schema_name="arc_playwright_failure_analysis",
                    instructions=FAILURE_SYNTHESIS_INSTRUCTIONS,
                    input_payload={
                        "requirement_id": test_run.requirement_id,
                        "compiler_routing": routing,
                        "failed_playwright_tests": failure_log,
                    },
                    output_schema=FAILURE_SYNTHESIS_SCHEMA,
                )
                synthesis = str(decision.get("analysis", "")).strip()
            except Exception as exc:
                synthesis = (
                    "The failure-analysis model was unavailable; use the failed-test logs below. "
                    f"Model error: {describe_model_error(exc)}"
                )
        else:
            synthesis = "No failure-analysis model was configured; use the failed-test logs below."
        annotated_failure_log = failure_log
        if failed_sections and synthesis:
            annotated_failure_log = "\n\n".join(
                f"{section}\n"
                "failure_analysis:\n"
                f"{synthesis}"
                for section in failed_sections
            )
        return (
            "PLAYWRIGHT FAILURE ANALYSIS\n"
            f"Requirement: {test_run.requirement_id}\n\n"
            "Model analysis:\n"
            f"{synthesis}\n\n"
            "Compiler routing hints (not additional test output):\n"
            f"{routing}\n\n"
            "Failed Playwright test logs (JSON reporter + pw:api):\n"
            f"{annotated_failure_log}"
        )

    def _failed_test_sections(
        self,
        *,
        failed_tests: list[dict[str, Any]],
        reports: list[TestFailureReport],
        test_rows: list[dict[str, Any]],
        binding_by_id: dict[str, dict[str, Any]],
        pw_api_lines: list[str],
    ) -> list[str]:
        sections: list[str] = []
        for failed in failed_tests:
            raw_test_file = str(failed.get("file", ""))
            test_file = self._workspace_file(raw_test_file) or _normalize_relative(
                raw_test_file
            )
            title = str(failed.get("title", "")).strip() or "(untitled test)"
            matching_rows = [
                row
                for row in test_rows
                if _normalize_relative(str(row.get("test_file", ""))) == test_file
                and str(row.get("title", "")).strip() == title
            ]
            if not matching_rows:
                matching_rows = [
                    row
                    for row in test_rows
                    if _normalize_relative(str(row.get("test_file", ""))) == test_file
                    and str(row.get("title", "")).strip()
                    and str(row.get("title", "")).strip() in title
                ]
            module_ids = sorted(
                {
                    str(module)
                    for row in matching_rows
                    for module in row.get("target_modules", [])
                    if str(module)
                }
            )
            source_files = sorted(
                {
                    str(binding_by_id[module].get("file", ""))
                    for module in module_ids
                    if module in binding_by_id and str(binding_by_id[module].get("file", ""))
                }
            )
            test_id = ", ".join(
                sorted({str(row.get("test_id", "")) for row in matching_rows if row.get("test_id")})
            ) or "(unmatched manifest test)"
            error_text = str(failed.get("error", "")).strip() or "(no structured error)"
            call_log = str(failed.get("call_log", "")).strip() or "(no Playwright call log)"
            stack = str(failed.get("stack", "")).strip() or "(no stack)"
            source_context = self._source_context(
                source_files=source_files,
                reports=reports,
                module_ids=set(module_ids),
            )
            sections.append(
                f"FAILED TEST: {title}\n"
                f"test_id: {test_id}\n"
                f"test_file: {test_file or '(unknown)'}\n"
                f"test_module: {', '.join(module_ids) or '(not localized)'}\n"
                f"implementation_source_files: {', '.join(source_files) or '(not localized)'}\n"
                f"failure_analysis_input_error: {error_text}\n"
                f"key_failure_log:\n{call_log}\n"
                f"pw:api_stop_trace:\n{chr(10).join(pw_api_lines) or '(no pw:api lines captured)'}\n"
                f"stack:\n{stack}\n"
                f"relevant_source:\n{source_context}"
            )
        return sections

    def _source_context(
        self,
        *,
        source_files: list[str],
        reports: list[TestFailureReport],
        module_ids: set[str],
    ) -> str:
        frames = [
            frame
            for report in reports
            if module_ids.intersection(report.target_modules)
            for frame in report.stack_frames
        ]
        for relative in source_files:
            line = next(
                (frame.line for frame in frames if _normalize_relative(frame.file) == _normalize_relative(relative)),
                None,
            )
            excerpt = _read_source_excerpt(self.output_root, relative, line=line)
            if excerpt:
                return f"// {relative}\n{excerpt}"
        return "(source excerpt unavailable; consult writable_source_files in stable context)"

    def _report_for_command(
        self,
        *,
        requirement_id: str,
        iteration: int,
        command_result: TestCommandResult,
        test_rows: list[dict[str, Any]],
        resolved_targets: dict[str, Any],
        binding_by_id: dict[str, dict[str, Any]],
        modules_by_file: dict[str, list[str]],
        changed_files: list[str],
        owned_module_ids: set[str],
    ) -> TestFailureReport:
        output = _clean_output(
            "\n".join(
                value
                for value in (
                    command_result.error or "",
                    command_result.stderr,
                    command_result.stdout,
                )
                if value
            )
        )
        phase = _failure_phase(command_result, output)
        deferred = sorted(
            {
                str(value)
                for value in command_result.stub_hits
                if str(value) and str(value) not in owned_module_ids
            }
        )
        failure_class = _failure_class(
            command_result,
            phase,
            output,
            deferred_modules=deferred,
        )
        matching_tests = _matching_tests(command_result, output, test_rows)
        test_ids = sorted(
            {
                str(row.get("test_id", ""))
                for row in matching_tests
                if str(row.get("test_id", ""))
            }
        )
        stack_frames = self._stack_frames(output)
        target_modules = {
            str(value)
            for row in matching_tests
            for value in row.get("target_modules", [])
            if str(value)
        }
        for frame in stack_frames:
            target_modules.update(modules_by_file.get(frame.file, []))
        writable_targets, read_only_targets = _relevant_targets(
            resolved_targets,
            binding_by_id,
            target_modules,
        )
        message = _failure_message(output, command_result)
        fingerprint = _fingerprint(
            failure_class=failure_class,
            phase=phase,
            layer=command_result.layer,
            test_ids=test_ids,
            message=message,
            target_modules=target_modules,
        )
        return TestFailureReport(
            requirement_id=requirement_id,
            iteration=max(0, int(iteration)),
            test_id=test_ids[0] if len(test_ids) == 1 else None,
            test_ids=test_ids,
            layer=command_result.layer,
            phase=phase,
            failure_class=failure_class,
            message=message,
            stack_frames=stack_frames,
            target_modules=sorted(target_modules),
            writable_targets=writable_targets,
            read_only_dependencies=read_only_targets,
            changed_files=sorted({_normalize_relative(value) for value in changed_files}),
            failure_fingerprint=fingerprint,
            command=list(command_result.command),
            diagnostic_output=output,
            deferred_dependency_modules=deferred,
        )

    def _report_for_runner_failure(
        self,
        *,
        requirement_id: str,
        iteration: int,
        test_run: TestRunResult,
        resolved_targets: dict[str, Any],
        binding_by_id: dict[str, dict[str, Any]],
        changed_files: list[str],
    ) -> TestFailureReport:
        message = "\n".join(test_run.errors) or f"Test run ended with {test_run.status}."
        failure_class = (
            "TEST_OR_CONTRACT_INCONSISTENT"
            if any(
                token in message
                for token in ("TEST_INTEGRITY_FAILED", "TEST_MANIFEST_INVALID")
            )
            else "INFRASTRUCTURE"
        )
        writable_targets, read_only_targets = _relevant_targets(
            resolved_targets,
            binding_by_id,
            set(),
        )
        fingerprint = _fingerprint(
            failure_class=failure_class,
            phase="RUNNER",
            layer=None,
            test_ids=test_run.selected_test_ids,
            message=message,
            target_modules=set(),
        )
        return TestFailureReport(
            requirement_id=requirement_id,
            iteration=max(0, int(iteration)),
            test_id=(
                test_run.selected_test_ids[0]
                if len(test_run.selected_test_ids) == 1
                else None
            ),
            test_ids=sorted(set(test_run.selected_test_ids)),
            layer=None,
            phase="RUNNER",
            failure_class=failure_class,
            message=message,
            stack_frames=[],
            target_modules=[],
            writable_targets=writable_targets,
            read_only_dependencies=read_only_targets,
            changed_files=sorted({_normalize_relative(value) for value in changed_files}),
            failure_fingerprint=fingerprint,
            diagnostic_output=message,
        )

    def _stack_frames(self, output: str) -> list[StackFrame]:
        frames: list[StackFrame] = []
        seen: set[tuple[str, int | None, int | None, str | None]] = set()
        pattern = re.compile(
            r"(?:^|\s)(?:at\s+(?:(?P<symbol>[^\s(]+)\s+)?\()?"
            r"(?P<file>(?:file://)?(?:[A-Za-z]:[\\/]|/)?[^\s():]+\.(?:ts|tsx|js|jsx))"
            r":(?P<line>\d+)(?::(?P<column>\d+))?",
            re.MULTILINE,
        )
        for match in pattern.finditer(output):
            file = self._workspace_file(match.group("file"))
            if file is None or "/node_modules/" in f"/{file}":
                continue
            frame = StackFrame(
                file=file,
                line=int(match.group("line")) if match.group("line") else None,
                column=(
                    int(match.group("column")) if match.group("column") else None
                ),
                symbol=match.group("symbol"),
            )
            key = (frame.file, frame.line, frame.column, frame.symbol)
            if key not in seen:
                seen.add(key)
                frames.append(frame)
            if len(frames) >= 12:
                break
        return frames

    def _workspace_file(self, value: str) -> str | None:
        raw = value.removeprefix("file://").replace("\\", "/")
        path = Path(raw)
        if path.is_absolute():
            try:
                return path.resolve().relative_to(self.output_root).as_posix()
            except ValueError:
                return None
        normalized = _normalize_relative(raw)
        if normalized.startswith("../"):
            return None
        return normalized

    @staticmethod
    def _validate_inputs(
        *,
        requirement_id: str,
        code_binding_registry: dict[str, Any],
        test_manifest: dict[str, Any],
        iteration: int,
    ) -> list[str]:
        errors: list[str] = []
        if not requirement_id:
            errors.append("ARC4510 FAILURE_ANALYSIS_INVALID: requirement_id is required.")
        if code_binding_registry.get("status") != CODE_BINDING_READY:
            errors.append(
                "ARC4510 FAILURE_ANALYSIS_INVALID: Code Binding Registry is not ready."
            )
        if test_manifest.get("status") != TESTS_FROZEN:
            errors.append(
                "ARC4510 FAILURE_ANALYSIS_INVALID: Test Manifest is not frozen."
            )
        if not isinstance(test_manifest.get("tests"), list):
            errors.append(
                "ARC4510 FAILURE_ANALYSIS_INVALID: Test Manifest has no tests table."
            )
        if not isinstance(iteration, int) or iteration < 0:
            errors.append(
                "ARC4510 FAILURE_ANALYSIS_INVALID: iteration must be a non-negative integer."
            )
        if not errors:
            try:
                CodeTargetResolver(code_binding_registry).resolve_requirement_targets(
                    requirement_id
                )
            except KeyError as exc:
                errors.append(f"ARC4510 FAILURE_ANALYSIS_INVALID: {exc}")
        return errors


def _matching_tests(
    command: TestCommandResult,
    output: str,
    test_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    file_set = {_normalize_relative(value) for value in command.test_files}
    candidates = [
        row
        for row in test_rows
        if _normalize_relative(str(row.get("test_file", ""))) in file_set
        and (command.layer is None or str(row.get("layer", "")).upper() == command.layer)
    ]
    title_matches = [
        row
        for row in candidates
        if str(row.get("title", "")).strip()
        and str(row.get("title", "")).strip() in output
    ]
    return title_matches or candidates


def _playwright_failed_tests(raw_report: str) -> list[dict[str, Any]]:
    """Extract failed specs from Playwright's JSON reporter output."""

    payload = _decode_playwright_json(raw_report)
    if not payload:
        return []
    failed: list[dict[str, Any]] = []

    def walk_suite(suite: dict[str, Any], inherited_file: str = "") -> None:
        suite_file = str(suite.get("file") or inherited_file)
        for spec in suite.get("specs", []):
            if not isinstance(spec, dict):
                continue
            location = spec.get("location")
            location_file = location.get("file", "") if isinstance(location, dict) else ""
            spec_file = str(spec.get("file") or location_file or suite_file)
            results: list[dict[str, Any]] = []
            for test in spec.get("tests", []):
                if not isinstance(test, dict):
                    continue
                for result in test.get("results", []):
                    if isinstance(result, dict):
                        results.append(result)
            failed_results = [
                result
                for result in results
                if str(result.get("status", "")).lower()
                not in {"passed", "skipped", "pending"}
            ]
            if not failed_results and spec.get("ok") is not False:
                continue
            result = failed_results[-1] if failed_results else (results[-1] if results else {})
            error = _playwright_error(result.get("error"))
            errors = [
                _playwright_error(value)
                for value in result.get("errors", [])
                if _playwright_error(value)
            ]
            if errors and error:
                error = "\n".join([error, *errors])
            elif errors:
                error = "\n".join(errors)
            failed.append(
                {
                    "file": spec_file,
                    "title": str(spec.get("title", "")),
                    "error": error,
                    "call_log": _extract_call_log(error),
                    "stack": _extract_stack(error),
                    "status": str(result.get("status", "failed")),
                }
            )
        for child in suite.get("suites", []):
            if isinstance(child, dict):
                walk_suite(child, suite_file)

    for suite in payload.get("suites", []):
        if isinstance(suite, dict):
            walk_suite(suite)
    return failed


def _decode_playwright_json(raw_report: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw_report):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(raw_report[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and isinstance(candidate.get("suites"), list):
            return candidate
    return None


def _playwright_error(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return str(value).strip() if value is not None else ""
    message = str(value.get("message", "")).strip()
    stack = str(value.get("stack", "")).strip()
    if stack and stack != message:
        return "\n".join(value for value in (message, stack) if value)
    return message or stack


def _extract_call_log(error: str) -> str:
    marker = "Call log:"
    if marker in error:
        return error[error.index(marker) :].strip()
    return error


def _extract_stack(error: str) -> str:
    marker = "Call log:"
    if marker in error:
        return error[: error.index(marker)].strip()
    return error


def _fallback_e2e_failure_log(
    *,
    reports: list[TestFailureReport],
    commands: list[TestCommandResult],
    raw_report: str,
) -> str:
    command_text = "\n".join(
        " ".join(command.command) for command in commands if command.command
    )
    return (
        "FAILED TEST: (Playwright JSON could not be decoded)\n"
        f"command: {command_text or '(unknown)'}\n"
        f"failure_class: {', '.join(sorted({report.failure_class for report in reports}))}\n"
        "key_failure_log:\n"
        f"{raw_report or '(Playwright produced no report text.)'}"
    )


def _read_source_excerpt(root: Path, relative: str, *, line: int | None) -> str:
    normalized = _normalize_relative(relative)
    if not normalized:
        return ""
    path = (root / normalized).resolve()
    try:
        path.relative_to(root.resolve())
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return ""
    if not lines:
        return ""
    if line is None or line < 1:
        start, end = 0, min(len(lines), 40)
    else:
        start = max(0, line - 8)
        end = min(len(lines), line + 8)
    return "\n".join(
        f"{index + 1:04d}: {lines[index]}" for index in range(start, end)
    )


def _failure_phase(command: TestCommandResult, output: str) -> str:
    lowered = output.lower()
    if command.phase == "TYPECHECK":
        return "TYPECHECK"
    if command.phase == "EXECUTION" and command.timed_out:
        return "ASSERTION"
    if command.error or any(
        token in lowered
        for token in (
            "econnrefused",
            "address already in use",
            "webserver process",
            "browser executable",
            "failed to launch",
        )
    ):
        return "BOOTSTRAP"
    if any(
        token in lowered
        for token in (
            "failed to resolve import",
            "cannot find module",
            "no test files found",
            "transform failed",
            "syntaxerror",
        )
    ):
        return "COLLECTION"
    if command.layer == "E2E" and any(
        token in lowered
        for token in ("tohavescreenshot", "screenshot", "pixelmatch", "visual")
    ):
        return "VISUAL_ASSERTION"
    return "ASSERTION"


def _failure_class(
    command: TestCommandResult,
    phase: str,
    output: str,
    *,
    deferred_modules: list[str] | None = None,
) -> str:
    lowered = output.lower()
    if phase == "BOOTSTRAP":
        return "INFRASTRUCTURE"
    if phase == "COLLECTION":
        return "TEST_MATERIALIZATION"
    if phase == "TYPECHECK" or re.search(r"\berror\s+ts\d{4}\b", lowered):
        return "TYPE_CONTRACT"
    if phase == "VISUAL_ASSERTION":
        return "VISUAL_BEHAVIOR"
    if any(
        token in output
        for token in (
            "TEST_INTEGRITY_FAILED",
            "TEST_MANIFEST_INVALID",
            "TEST_OR_CONTRACT_INCONSISTENT",
        )
    ):
        return "TEST_OR_CONTRACT_INCONSISTENT"
    # Checked last, so a real type error or a broken test still wins: a behavioral
    # failure that ran through another requirement's skeleton says nothing about this
    # requirement's code, and routing it to the implementation agent would only buy a
    # patch that works around a module the schedule builds later.
    if deferred_modules:
        return "DEFERRED_DEPENDENCY"
    return "IMPLEMENTATION_BEHAVIOR"


def _failure_message(output: str, command: TestCommandResult) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    meaningful = [
        line
        for line in lines
        if not re.fullmatch(r"[-=─━\s]+", line)
        and not line.startswith(("at node:", "npm error A complete log"))
    ]
    if meaningful:
        return "\n".join(meaningful[-12:])[-4000:]
    if command.error:
        return command.error
    return f"{command.phase} {command.layer or ''} exited with {command.returncode}.".strip()


def _relevant_targets(
    resolved: dict[str, Any],
    binding_by_id: dict[str, dict[str, Any]],
    target_modules: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    writable_ids = {str(value) for value in resolved.get("writable", []) if str(value)}
    read_only_ids = {str(value) for value in resolved.get("read_only", []) if str(value)}
    relevant = set(target_modules)
    if relevant:
        writable_ids &= relevant
        read_only_ids &= relevant
    return (
        [_target_card(binding_by_id[value]) for value in sorted(writable_ids) if value in binding_by_id],
        [_target_card(binding_by_id[value]) for value in sorted(read_only_ids) if value in binding_by_id],
    )


def _target_card(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        key: binding.get(key)
        for key in (
            "module_id",
            "kind",
            "file",
            "symbol",
            "editable",
            "implementation_region",
        )
    }


def _fingerprint(
    *,
    failure_class: str,
    phase: str,
    layer: str | None,
    test_ids: list[str],
    message: str,
    target_modules: set[str],
) -> str:
    normalized_message = re.sub(r"\b\d+(?:\.\d+)?\s*m?s\b", "<duration>", message)
    normalized_message = re.sub(
        r"\b[0-9a-f]{16,}\b",
        "<dynamic>",
        normalized_message,
        flags=re.IGNORECASE,
    )
    normalized_message = re.sub(r":\d+:\d+\b", ":<line>:<column>", normalized_message)
    payload = {
        "failure_class": failure_class,
        "phase": phase,
        "layer": layer,
        "test_ids": sorted(test_ids),
        "message": normalized_message,
        "target_modules": sorted(target_modules),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _clean_output(value: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value).replace("\r\n", "\n")


def _normalize_relative(value: str) -> str:
    normalized = str(value).replace("\\", "/").strip().strip("/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized
