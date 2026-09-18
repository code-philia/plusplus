from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .code_binding import CODE_BINDING_READY, CodeTargetResolver
from .test_generation import TESTS_FROZEN
from .test_runner import TestCommandResult, TestRunResult


FAILURE_ANALYSIS_SCHEMA_VERSION = 1
FAILURE_CLASSES = (
    "INFRASTRUCTURE",
    "TEST_MATERIALIZATION",
    "TYPE_CONTRACT",
    "IMPLEMENTATION_BEHAVIOR",
    "VISUAL_BEHAVIOR",
    "TEST_OR_CONTRACT_INCONSISTENT",
)


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FailureAnalysisResult:
    requirement_id: str
    reports: list[TestFailureReport]
    errors: list[str] = field(default_factory=list)
    schema_version: int = FAILURE_ANALYSIS_SCHEMA_VERSION

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FailureAnalyzer:
    """Convert runner output into requirement- and binding-aware failure reports."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.expanduser().resolve()

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
        return FailureAnalysisResult(requirement_id=requirement_id, reports=reports)

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
        failure_class = _failure_class(command_result, phase, output)
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


def _failure_class(command: TestCommandResult, phase: str, output: str) -> str:
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
