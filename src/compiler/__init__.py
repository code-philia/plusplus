"""Public ARC compiler interface."""

from .driver import ARCCompiler, Compiler
from .passes.linking.code_binding import CodeTargetResolver, resolve_requirement_targets
from .exact_file_patcher import ExactFilePatcher, FilePatchResult
from .models import CompilationRequest
from .test_runner import TestRunResult, TestRunner, TestSelection
from synthesis.tdd.orchestrator import NodeTDDOrchestrator, NodeTDDPolicy, TDDStageResult
from agents.contracts import ProposedEdit, ProposedPatch

__all__ = ["ARCCompiler",
    "CodeTargetResolver",
    "CompilationRequest",
    "Compiler",
    "ExactFilePatcher",
    "FilePatchResult",
    "NodeTDDOrchestrator",
    "NodeTDDPolicy",
    "TDDStageResult",
    "ProposedEdit",
    "ProposedPatch",
    "TestRunResult",
    "TestRunner",
    "TestSelection",
    "resolve_requirement_targets",
]