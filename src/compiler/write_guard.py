from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from arc_agents.contracts import ProposedEdit, ProposedPatch

from .code_binding import CODE_BINDING_READY, CodeTargetResolver


WRITE_GUARD_SCHEMA_VERSION = 1


@dataclass(slots=True)
class AppliedEdit:
    module_id: str
    file: str
    before_sha256: str
    after_sha256: str


@dataclass(slots=True)
class ApplyResult:
    requirement_id: str
    status: str
    changed_files: list[str] = field(default_factory=list)
    changed_modules: list[str] = field(default_factory=list)
    applied_edits: list[AppliedEdit] = field(default_factory=list)
    rejected_changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: int = WRITE_GUARD_SCHEMA_VERSION

    @property
    def ok(self) -> bool:
        return self.status in {"VALIDATED", "APPLIED"} and not self.rejected_changes

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _PlannedFile:
    relative: str
    target: Path
    original: str
    updated: str
    before_sha256: str
    after_sha256: str
    module_ids: list[str]


class WriteGuard:
    """Validate and apply exact fragment replacements for one requirement."""

    def __init__(
        self,
        output_root: Path,
        *,
        requirement_ir: dict[str, Any] | None = None,
    ) -> None:
        self.output_root = output_root.expanduser().resolve()
        self.requirement_ir = requirement_ir or {}

    def apply(
        self,
        patch: ProposedPatch,
        *,
        code_binding_registry: dict[str, Any],
        dry_run: bool = False,
    ) -> ApplyResult:
        requirement_id = str(patch.requirement_id).strip()
        rejected: list[str] = []
        if not requirement_id:
            rejected.append("ARC4520 PATCH_INVALID: requirement_id is required.")
        if code_binding_registry.get("status") != CODE_BINDING_READY:
            rejected.append("ARC4520 PATCH_INVALID: Code Binding Registry is not ready.")
        if not patch.edits:
            rejected.append("ARC4520 PATCH_INVALID: at least one edit is required.")
        module_ids = [str(edit.module_id).strip() for edit in patch.edits]
        duplicate_ids = sorted(
            module_id for module_id in set(module_ids) if module_ids.count(module_id) > 1
        )
        if duplicate_ids:
            rejected.append(
                f"ARC4520 PATCH_INVALID: duplicate module edits {duplicate_ids}."
            )
        if rejected:
            return self._rejected(requirement_id, rejected)

        try:
            resolved = CodeTargetResolver(
                code_binding_registry
            ).resolve_requirement_targets(requirement_id)
        except KeyError as exc:
            return self._rejected(
                requirement_id,
                [f"ARC4521 PATCH_REQUIREMENT_UNKNOWN: {exc}"],
            )
        writable_ids = {str(value) for value in resolved.get("writable", []) if str(value)}
        bindings = {
            str(row.get("module_id", "")): row
            for row in code_binding_registry.get("code_bindings", [])
            if isinstance(row, dict) and str(row.get("module_id", ""))
        }

        edits_by_file: dict[str, list[tuple[ProposedEdit, dict[str, Any]]]] = {}
        for edit in patch.edits:
            module_id = str(edit.module_id).strip()
            binding = bindings.get(module_id)
            if binding is None:
                rejected.append(f"ARC4522 PATCH_TARGET_UNKNOWN: {module_id}.")
                continue
            if module_id not in writable_ids:
                rejected.append(
                    f"ARC4523 PATCH_TARGET_READ_ONLY: {module_id} is not writable for "
                    f"{requirement_id}."
                )
                continue
            region = binding.get("implementation_region")
            if not bool(binding.get("editable")) or not isinstance(region, dict):
                rejected.append(
                    f"ARC4523 PATCH_TARGET_READ_ONLY: {module_id} has no editable region."
                )
                continue
            relative = _safe_relative_source(str(binding.get("file", "")))
            if relative is None:
                rejected.append(
                    f"ARC4524 PATCH_PATH_INVALID: unsafe source path for {module_id}."
                )
                continue
            replacement_error = _replacement_error(edit.replacement)
            if replacement_error:
                rejected.append(f"ARC4525 PATCH_CONTENT_INVALID: {module_id}: {replacement_error}")
                continue
            seed_error = self._seed_data_error(
                requirement_id=requirement_id,
                binding=binding,
                replacement=edit.replacement,
            )
            if seed_error:
                rejected.append(f"ARC4553 SEED_DATA_IN_REPOSITORY: {module_id}: {seed_error}")
                continue
            edits_by_file.setdefault(relative, []).append((edit, binding))
        if rejected:
            return self._rejected(requirement_id, rejected)

        planned_files: list[_PlannedFile] = []
        applied_edits: list[AppliedEdit] = []
        warnings: list[str] = []
        for relative, file_edits in sorted(edits_by_file.items()):
            target = (self.output_root / Path(relative)).resolve()
            if self.output_root not in target.parents or not target.is_file():
                rejected.append(
                    f"ARC4524 PATCH_PATH_INVALID: source file does not exist: {relative}."
                )
                continue
            try:
                original = _read_source(target)
                original_bytes = target.read_bytes()
            except (OSError, UnicodeError) as exc:
                rejected.append(f"ARC4524 PATCH_PATH_INVALID: cannot read {relative}: {exc}")
                continue
            before_sha256 = hashlib.sha256(original_bytes).hexdigest()
            expected_hashes = {str(edit.expected_sha256).lower() for edit, _ in file_edits}
            if len(expected_hashes) != 1 or not all(
                re.fullmatch(r"[0-9a-f]{64}", value) for value in expected_hashes
            ):
                rejected.append(
                    f"ARC4526 PATCH_BASE_INVALID: {relative} needs one valid expected SHA-256."
                )
                continue
            if before_sha256 not in expected_hashes:
                rejected.append(
                    f"ARC4526 PATCH_BASE_INVALID: {relative} changed after the patch was proposed."
                )
                continue

            updated = original
            file_applied: list[AppliedEdit] = []
            for edit, binding in file_edits:
                module_id = str(edit.module_id).strip()
                region = binding["implementation_region"]
                start_marker = str(region.get("start_marker", ""))
                end_marker = str(region.get("end_marker", ""))
                module_marker = str(binding.get("module_marker", ""))
                marker_error = _marker_error(
                    updated,
                    module_id=module_id,
                    module_marker=module_marker,
                    start_marker=start_marker,
                    end_marker=end_marker,
                )
                if marker_error:
                    rejected.append(marker_error)
                    break
                candidate, exact_error = _replace_exact(
                    updated,
                    start_marker=start_marker,
                    end_marker=end_marker,
                    search=edit.search,
                    replacement=edit.replacement,
                )
                if exact_error:
                    rejected.append(
                        f"ARC4529 PATCH_SEARCH_INVALID: {module_id}: {exact_error}"
                    )
                    break
                if candidate == updated:
                    warnings.append(
                        f"ARC4528 PATCH_NO_CHANGES: ignored no-op edit for {module_id}."
                    )
                    continue
                updated = candidate
                file_applied.append(
                    AppliedEdit(
                        module_id=module_id,
                        file=relative,
                        before_sha256=before_sha256,
                        after_sha256="",
                    )
                )
            if rejected:
                continue
            after_sha256 = hashlib.sha256(updated.encode("utf-8")).hexdigest()
            if updated == original:
                continue
            for row in file_applied:
                row.after_sha256 = after_sha256
            applied_edits.extend(file_applied)
            planned_files.append(
                _PlannedFile(
                    relative=relative,
                    target=target,
                    original=original,
                    updated=updated,
                    before_sha256=before_sha256,
                    after_sha256=after_sha256,
                    module_ids=[row.module_id for row in file_applied],
                )
            )

        if rejected:
            return self._rejected(requirement_id, rejected)
        if not planned_files:
            return self._rejected(
                requirement_id,
                ["ARC4528 PATCH_NO_CHANGES: proposed edits do not change any writable source."],
            )
        if dry_run:
            return ApplyResult(
                requirement_id=requirement_id,
                status="VALIDATED",
                changed_files=[row.relative for row in planned_files],
                changed_modules=sorted(
                    module_id for row in planned_files for module_id in row.module_ids
                ),
                applied_edits=applied_edits,
                warnings=warnings,
            )

        written: list[_PlannedFile] = []
        try:
            for planned in planned_files:
                _write_source_atomic(planned.target, planned.updated)
                written.append(planned)
        except OSError as exc:
            rollback_errors: list[str] = []
            for planned in reversed(written):
                try:
                    _write_source_atomic(planned.target, planned.original)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{planned.relative}: {rollback_exc}")
            message = f"ARC4529 PATCH_WRITE_FAILED: {exc}"
            if rollback_errors:
                message += f"; rollback failed for {rollback_errors}"
            return self._rejected(requirement_id, [message])

        return ApplyResult(
            requirement_id=requirement_id,
            status="APPLIED",
            changed_files=[row.relative for row in planned_files],
            changed_modules=sorted(
                module_id for row in planned_files for module_id in row.module_ids
            ),
            applied_edits=applied_edits,
            warnings=warnings,
        )

    def snapshot(self, relative_files: list[str]) -> dict[str, str]:
        """Capture the current text of every file a node is allowed to touch.

        The TDD orchestrator uses this as a source checkpoint. It may be the
        generated skeleton initially, then is replaced after a workspace
        typecheck passes.
        """

        captured: dict[str, str] = {}
        for value in dict.fromkeys(relative_files):
            relative = _safe_relative_source(str(value))
            if relative is None:
                continue
            target = (self.output_root / Path(relative)).resolve()
            if self.output_root not in target.parents or not target.is_file():
                continue
            try:
                captured[relative] = _read_source(target)
            except (OSError, UnicodeError):
                continue
        return captured

    def restore(self, snapshot: dict[str, str]) -> tuple[list[str], list[str]]:
        """Put every snapshotted file back, reporting what changed and what failed."""

        restored: list[str] = []
        errors: list[str] = []
        for relative, original in sorted(snapshot.items()):
            safe = _safe_relative_source(str(relative))
            if safe is None:
                errors.append(f"unsafe snapshot path {relative!r}")
                continue
            target = (self.output_root / Path(safe)).resolve()
            if self.output_root not in target.parents:
                errors.append(f"snapshot path escapes workspace: {safe}")
                continue
            try:
                if target.is_file() and _read_source(target) == original:
                    continue
                _write_source_atomic(target, original)
            except (OSError, UnicodeError) as exc:
                errors.append(f"{safe}: {exc}")
                continue
            restored.append(safe)
        return restored, errors

    def _seed_data_error(
        self,
        *,
        requirement_id: str,
        binding: dict[str, Any],
        replacement: str,
    ) -> str | None:
        if str(binding.get("kind", "")).upper() != "DB":
            return None
        nodes = self.requirement_ir.get("nodes", {})
        node = nodes.get(requirement_id, {}) if isinstance(nodes, dict) else {}
        fixtures = node.get("seed_fixtures", []) if isinstance(node, dict) else []
        if not fixtures:
            return None
        lowered = replacement.casefold()
        if re.search(r"\bseed\s+data\b|\bfixture(?:s)?\b", lowered):
            return "DB implementation regions must not contain fixture setup."
        description_literals = {
            match.group(1).strip().casefold()
            for fixture in fixtures
            if isinstance(fixture, dict)
            for match in re.finditer(r'["“]([^"”]{4,})["”]', str(fixture.get("description", "")))
        }
        row_literals = {
            str(value).casefold()
            for fixture in fixtures
            if isinstance(fixture, dict)
            for row in fixture.get("rows", [])
            if isinstance(row, dict)
            for value in row.get("values", {}).values()
            if isinstance(value, str) and len(value.strip()) >= 4
        }
        literals = description_literals | row_literals
        leaked = sorted(value for value in literals if value and value in lowered)
        if leaked:
            return f"fixture literals belong in seed setup, not DB reads/writes: {leaked}."
        return None

    @staticmethod
    def _rejected(requirement_id: str, messages: list[str]) -> ApplyResult:
        return ApplyResult(
            requirement_id=requirement_id,
            status="REJECTED",
            rejected_changes=list(dict.fromkeys(messages)),
        )


def _safe_relative_source(value: str) -> str | None:
    normalized = str(value).replace("\\", "/").strip().strip("/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or normalized.startswith(("tests/", ".arc/"))
        or not normalized.startswith(("backend/src/", "frontend/src/"))
        or not normalized.endswith((".ts", ".tsx"))
    ):
        return None
    return normalized


def _replacement_error(value: str) -> str | None:
    if not isinstance(value, str):
        return "replacement must be text."
    if "\x00" in value:
        return "replacement contains a null byte."
    if len(value.encode("utf-8")) > 200_000:
        return "replacement exceeds 200000 UTF-8 bytes."
    if "ARC-IMPLEMENTATION-BEGIN:" in value or "ARC-IMPLEMENTATION-END:" in value:
        return "replacement must not contain implementation markers."
    if "@arc-module" in value:
        return "replacement must not contain a module marker."
    if re.search(r"(?m)^\s*(?:import|export)\s", value):
        return "replacement must not contain a file-level import or export."
    return None


def _marker_error(
    source: str,
    *,
    module_id: str,
    module_marker: str,
    start_marker: str,
    end_marker: str,
) -> str | None:
    if not start_marker or not end_marker:
        return f"ARC4527 PATCH_REGION_INVALID: {module_id} has incomplete markers."
    if module_marker and source.count(module_marker) != 1:
        return f"ARC4527 PATCH_REGION_INVALID: {module_id} module marker is missing or ambiguous."
    if source.count(start_marker) != 1 or source.count(end_marker) != 1:
        return (
            f"ARC4527 PATCH_REGION_INVALID: {module_id} implementation markers are "
            "missing or ambiguous."
        )
    start = source.index(start_marker)
    end = source.index(end_marker)
    start_line_end = source.find("\n", start + len(start_marker))
    end_line_start = source.rfind("\n", 0, end)
    if start >= end or start_line_end < 0 or end_line_start < start_line_end:
        return f"ARC4527 PATCH_REGION_INVALID: {module_id} marker order is invalid."
    return None


def _replace_region(
    source: str,
    *,
    start_marker: str,
    end_marker: str,
    replacement: str,
) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker)
    region_start = source.find("\n", start + len(start_marker)) + 1
    region_end = source.rfind("\n", 0, end) + 1
    newline = "\r\n" if "\r\n" in source else "\n"
    normalized = replacement.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.strip("\n")
    body = normalized.replace("\n", newline)
    if body:
        body += newline
    return source[:region_start] + body + source[region_end:]


def _replace_exact(
    source: str,
    *,
    start_marker: str,
    end_marker: str,
    search: str,
    replacement: str,
) -> tuple[str, str | None]:
    """Replace one exact fragment, constrained to a module implementation region."""

    start = source.index(start_marker)
    end = source.index(end_marker)
    region_start = source.find("\n", start + len(start_marker)) + 1
    region_end = source.rfind("\n", 0, end) + 1
    region = source[region_start:region_end]
    if not isinstance(search, str) or not search.strip():
        return source, "search fragment must be non-empty."
    normalized_search = search.replace("\r\n", "\n").replace("\r", "\n")
    normalized_region = region.replace("\r\n", "\n").replace("\r", "\n")
    count = normalized_region.count(normalized_search)
    if count == 0:
        return source, "search fragment was not found inside the implementation region."
    if count != 1:
        return source, f"search fragment is ambiguous inside the implementation region ({count} matches)."
    normalized_replacement = replacement.replace("\r\n", "\n").replace("\r", "\n")
    updated_region = normalized_region.replace(normalized_search, normalized_replacement, 1)
    newline = "\r\n" if "\r\n" in source else "\n"
    updated_region = updated_region.replace("\n", newline)
    return source[:region_start] + updated_region + source[region_end:], None


def _read_source(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_source_atomic(path: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.arc-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        temporary_path.chmod(path.stat().st_mode)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
