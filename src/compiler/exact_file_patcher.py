from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from arc_agents.contracts import ProposedPatch


FILE_PATCHER_SCHEMA_VERSION = 1


@dataclass(slots=True)
class AppliedFileEdit:
    file: str
    before_sha256: str
    after_sha256: str


@dataclass(slots=True)
class FilePatchResult:
    requirement_id: str
    status: str
    changed_files: list[str] = field(default_factory=list)
    changed_modules: list[str] = field(default_factory=list)
    applied_edits: list[AppliedFileEdit] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: int = FILE_PATCHER_SCHEMA_VERSION

    @property
    def ok(self) -> bool:
        return self.status == "APPLIED" and not self.errors

    @property
    def rejected_changes(self) -> list[str]:
        """Compatibility accessor for callers that display patching errors."""

        return self.errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _PendingFile:
    relative: str
    target: Path
    original: str
    updated: str
    before_sha256: str
    after_sha256: str


class ExactFilePatcher:
    """Apply model-proposed exact replacements and persist them atomically.

    This module is deliberately not an authorization or policy layer. It does
    not inspect requirement ownership, editable flags, module markers,
    imports/exports, routes, generated code, or replacement semantics. The
    implementation agent already receives the finite set of source files for
    its task. This class only resolves safe relative paths, performs exact text
    replacement, and writes the resulting files atomically.
    """

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.expanduser().resolve()

    def apply(
        self,
        patch: ProposedPatch,
        *,
        code_binding_registry: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> FilePatchResult:
        requirement_id = str(patch.requirement_id).strip()
        if not patch.edits:
            return self._failed(requirement_id, ["PATCH_EMPTY: no edits were proposed."])

        edits_by_file: dict[str, list[Any]] = {}
        for edit in patch.edits:
            relative = _safe_relative_file(str(edit.file))
            if relative is None:
                return self._failed(
                    requirement_id,
                    [f"PATCH_PATH_INVALID: unsafe source path {edit.file!r}."],
                )
            edits_by_file.setdefault(relative, []).append(edit)

        module_ids_by_file = _module_ids_by_file(code_binding_registry or {})
        pending: list[_PendingFile] = []
        applied_edits: list[AppliedFileEdit] = []
        warnings: list[str] = []

        for relative, edits in edits_by_file.items():
            target = (self.output_root / relative).resolve()
            if self.output_root not in target.parents or not target.is_file():
                return self._failed(
                    requirement_id,
                    [f"PATCH_PATH_INVALID: source file does not exist: {relative}."],
                )
            try:
                original = _read_source(target)
            except (OSError, UnicodeError) as exc:
                return self._failed(
                    requirement_id,
                    [f"PATCH_READ_FAILED: {relative}: {exc}"],
                )

            before_sha256 = hashlib.sha256(original.encode("utf-8")).hexdigest()
            updated = original
            file_edits: list[AppliedFileEdit] = []
            for edit in edits:
                candidate, error = _replace_exact(
                    updated,
                    search=edit.search,
                    replacement=edit.replacement,
                )
                if error:
                    return self._failed(
                        requirement_id,
                        [f"PATCH_SEARCH_FAILED: {relative}: {error}"],
                    )
                if candidate == updated:
                    warnings.append(f"PATCH_NO_CHANGES: ignored no-op edit for {relative}.")
                    continue
                updated = candidate
                file_edits.append(
                    AppliedFileEdit(
                        file=relative,
                        before_sha256=before_sha256,
                        after_sha256="",
                    )
                )

            if updated == original:
                continue
            after_sha256 = hashlib.sha256(updated.encode("utf-8")).hexdigest()
            for edit in file_edits:
                edit.after_sha256 = after_sha256
            applied_edits.extend(file_edits)
            pending.append(
                _PendingFile(
                    relative=relative,
                    target=target,
                    original=original,
                    updated=updated,
                    before_sha256=before_sha256,
                    after_sha256=after_sha256,
                )
            )

        if not pending:
            return self._failed(
                requirement_id,
                ["PATCH_NO_CHANGES: proposed edits did not change any source file."],
                warnings=warnings,
            )

        changed_files = [row.relative for row in pending]
        changed_modules = sorted(
            {
                module_id
                for relative in changed_files
                for module_id in module_ids_by_file.get(relative, [relative])
            }
        )
        if dry_run:
            return FilePatchResult(
                requirement_id=requirement_id,
                status="APPLIED",
                changed_files=changed_files,
                changed_modules=changed_modules,
                applied_edits=applied_edits,
                warnings=warnings,
            )

        written: list[_PendingFile] = []
        try:
            for row in pending:
                _write_source_atomic(row.target, row.updated)
                written.append(row)
        except OSError as exc:
            rollback_errors: list[str] = []
            for row in reversed(written):
                try:
                    _write_source_atomic(row.target, row.original)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{row.relative}: {rollback_exc}")
            message = f"PATCH_WRITE_FAILED: {exc}"
            if rollback_errors:
                message += f"; rollback failed for {rollback_errors}"
            return self._failed(requirement_id, [message], warnings=warnings)

        return FilePatchResult(
            requirement_id=requirement_id,
            status="APPLIED",
            changed_files=changed_files,
            changed_modules=changed_modules,
            applied_edits=applied_edits,
            warnings=warnings,
        )

    def snapshot(self, relative_files: list[str]) -> dict[str, str]:
        captured: dict[str, str] = {}
        for value in dict.fromkeys(relative_files):
            relative = _safe_relative_file(str(value))
            if relative is None:
                continue
            target = (self.output_root / relative).resolve()
            if self.output_root not in target.parents or not target.is_file():
                continue
            try:
                captured[relative] = _read_source(target)
            except (OSError, UnicodeError):
                continue
        return captured

    def restore(self, snapshot: dict[str, str]) -> tuple[list[str], list[str]]:
        restored: list[str] = []
        errors: list[str] = []
        for value, original in snapshot.items():
            relative = _safe_relative_file(str(value))
            if relative is None:
                errors.append(f"unsafe snapshot path {value!r}")
                continue
            target = (self.output_root / relative).resolve()
            if self.output_root not in target.parents:
                errors.append(f"snapshot path escapes workspace: {relative}")
                continue
            try:
                if target.is_file() and _read_source(target) == original:
                    continue
                _write_source_atomic(target, original)
            except (OSError, UnicodeError) as exc:
                errors.append(f"{relative}: {exc}")
                continue
            restored.append(relative)
        return restored, errors

    @staticmethod
    def _failed(
        requirement_id: str,
        errors: list[str],
        *,
        warnings: list[str] | None = None,
    ) -> FilePatchResult:
        return FilePatchResult(
            requirement_id=requirement_id,
            status="FAILED",
            errors=list(dict.fromkeys(errors)),
            warnings=list(dict.fromkeys(warnings or [])),
        )


def _safe_relative_file(value: str) -> str | None:
    normalized = str(value).replace("\\", "/").strip().strip("/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or normalized.startswith(("tests/", ".arc/"))
        or not normalized.startswith(("backend/src/", "frontend/src/"))
    ):
        return None
    return normalized


def _replace_exact(source: str, *, search: str, replacement: str) -> tuple[str, str | None]:
    if not isinstance(search, str) or not search:
        return source, "search fragment must be non-empty."
    if not isinstance(replacement, str):
        return source, "replacement must be text."
    normalized_source = source.replace("\r\n", "\n").replace("\r", "\n")
    normalized_search = search.replace("\r\n", "\n").replace("\r", "\n")
    count = normalized_source.count(normalized_search)
    if count == 0:
        return source, "search fragment was not found."
    if count != 1:
        return source, f"search fragment is ambiguous ({count} matches)."
    normalized_replacement = replacement.replace("\r\n", "\n").replace("\r", "\n")
    updated = normalized_source.replace(normalized_search, normalized_replacement, 1)
    newline = "\r\n" if "\r\n" in source else "\n"
    return updated.replace("\n", newline), None


def _module_ids_by_file(registry: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for row in registry.get("code_bindings", []):
        if not isinstance(row, dict):
            continue
        relative = _safe_relative_file(str(row.get("file", "")))
        module_id = str(row.get("module_id", "")).strip()
        if relative and module_id:
            result.setdefault(relative, []).append(module_id)
    return result


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
            prefix=f".{path.name}.patch-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)
