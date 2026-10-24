"""Static descriptions of implementation context; no validation or conversion."""
from typing import Any, Literal, NotRequired, TypedDict

from compiler.ir.type_contracts import TypeDefinition


class SourceContextFile(TypedDict):
    content: str
    exists: bool
    context_location: Literal['editable_files', 'read_only_files']


class CurrentTaskContext(TypedDict):
    requirement: dict[str, Any]
    phase: str
    data_access: dict[str, list[str]]
    recovery: NotRequired[dict[str, Any]]


class FailureContext(TypedDict):
    test_files: list[str]
    startup_files: NotRequired[list[str]]
    regression_requirements: list[dict[str, Any]]


class FileIndexEntry(TypedDict):
    file: str
    symbols: list[str]
    kind: str


class ImplementationContext(TypedDict):
    current_task: CurrentTaskContext
    current_contracts: list[dict[str, Any]]
    types: list[TypeDefinition]
    editable_files: dict[str, SourceContextFile]
    read_only_files: dict[str, SourceContextFile]
    shared_catalog: list[dict[str, Any]]
    related_catalog: list[dict[str, Any]]
    test_targets: list[str]
    gate_results: list[dict[str, Any]]
    failure_context: FailureContext
    context_state: dict[str, Any]
    file_index: list[FileIndexEntry]
    always_loaded_files: list[str]
