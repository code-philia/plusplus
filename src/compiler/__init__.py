"""Public ARC compiler interface."""

from .controller import Compiler
from .code_binding import CodeTargetResolver, resolve_requirement_targets
from .exact_file_patcher import AppliedFileEdit, ExactFilePatcher, FilePatchResult
from .failure_analysis import FailureAnalysisResult, FailureAnalyzer, TestFailureReport
from .models import CompilationRequest
from .test_runner import TestRunResult, TestRunner, TestSelection
from .tdd_orchestrator import NodeTDDOrchestrator, NodeTDDPolicy, NodeTDDResult
from arc_agents.contracts import ProposedEdit, ProposedPatch

__all__ = [
    "AppliedFileEdit",
    "CodeTargetResolver",
    "CompilationRequest",
    "Compiler",
    "FailureAnalysisResult",
    "FailureAnalyzer",
    "ExactFilePatcher",
    "FilePatchResult",
    "NodeTDDOrchestrator",
    "NodeTDDPolicy",
    "NodeTDDResult",
    "ProposedEdit",
    "ProposedPatch",
    "TestFailureReport",
    "TestRunResult",
    "TestRunner",
    "TestSelection",
    "resolve_requirement_targets",
]
