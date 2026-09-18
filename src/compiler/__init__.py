"""Public ARC compiler interface."""

from .controller import Compiler
from .code_binding import CodeTargetResolver, resolve_requirement_targets
from .failure_analysis import FailureAnalysisResult, FailureAnalyzer, TestFailureReport
from .models import CompilationRequest
from .test_runner import TestRunResult, TestRunner, TestSelection
from .tdd_orchestrator import NodeTDDOrchestrator, NodeTDDPolicy, NodeTDDResult
from .write_guard import ApplyResult, ProposedEdit, ProposedPatch, WriteGuard

__all__ = [
    "ApplyResult",
    "CodeTargetResolver",
    "CompilationRequest",
    "Compiler",
    "FailureAnalysisResult",
    "FailureAnalyzer",
    "NodeTDDOrchestrator",
    "NodeTDDPolicy",
    "NodeTDDResult",
    "ProposedEdit",
    "ProposedPatch",
    "TestFailureReport",
    "TestRunResult",
    "TestRunner",
    "TestSelection",
    "WriteGuard",
    "resolve_requirement_targets",
]
