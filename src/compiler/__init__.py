"""Public ARC compiler interface."""

from .driver import ARCCompiler, Compiler
from .passes.linking.binding.resolver import CodeTargetResolver, resolve_requirement_targets
from synthesis.tdd.patching import ExactFilePatcher, FilePatchResult
from .driver.models import CompilationRequest
from synthesis.tdd.runner import TestRunResult, TestRunner, TestSelection
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