"""Deterministic ARC compiler interface."""

from .backend_lowering import BackendGlueLowerer, BackendGlueResult
from .controller import Compiler
from .database_stage import DatabasePassResult, DatabaseSchemaPass
from .design_stage import DesignPass, DesignPassResult
from .file_planning import FilePlanningResult, GlobalFilePlanner
from .frontend_component_design import (
    FrontendDesignPassResult,
    PageLayoutComponentPass,
    finalize_frontend_design,
    frontend_design_traceability,
)
from .frontend_design import (
    REQUIREMENT_UI_SCOPE_SCHEMA,
    FrontendDesignState,
    RequirementUIScopePass,
    RequirementUIScopeResult,
    validate_frontend_design_minimum,
)
from .frontend_ir import (
    FRONTEND_DESIGN_IR_SCHEMA,
    FRONTEND_DESIGN_TABLE_SCHEMAS,
    FRONTEND_IR_SCHEMA_VERSION,
    FrontendDesignErrorCode,
    FrontendDesignIssue,
)
from .frontend_lowering import (
    FrontendFilePlanner,
    FrontendFilePlanningResult,
    FrontendGlobalSymbolPlanner,
    FrontendLoweringResult,
    FrontendSkeletonLowerer,
    FrontendSymbolPlanningResult,
)
from .model_client import Model, StructuredModel
from .module_lowering import ModuleSkeletonLowerer, ModuleSkeletonResult
from .models import CompilationRequest, CompilationResult
from .project_build import ProjectBuilder, ProjectBuildResult
from .project_initialization import (
    DependencyCatalog,
    ProjectInitializationResult,
    ProjectInitializer,
)
from .skeleton_lowering import (
    DatabaseSchemaLowerer,
    DatabaseSchemaLoweringResult,
    TypeLowerer,
    TypeLoweringResult,
)
from .symbol_planning import GlobalSymbolPlanner, SymbolPlanningResult
from .visual_reference import (
    ResolvedVisualReference,
    VisualModel,
    VisualModelConfigurationError,
    VisualReferenceAnalysisResult,
    VisualReferenceAnalyzer,
    VisualReferenceResolutionResult,
    VisualReferenceResolver,
    VisualStructuredModel,
)

__all__ = [
    "BackendGlueLowerer",
    "BackendGlueResult",
    "CompilationRequest",
    "CompilationResult",
    "Compiler",
    "DatabasePassResult",
    "DatabaseSchemaPass",
    "DatabaseSchemaLowerer",
    "DatabaseSchemaLoweringResult",
    "DependencyCatalog",
    "DesignPass",
    "DesignPassResult",
    "FilePlanningResult",
    "FRONTEND_DESIGN_IR_SCHEMA",
    "FRONTEND_DESIGN_TABLE_SCHEMAS",
    "FRONTEND_IR_SCHEMA_VERSION",
    "FrontendDesignErrorCode",
    "FrontendDesignIssue",
    "FrontendDesignPassResult",
    "FrontendDesignState",
    "FrontendFilePlanner",
    "FrontendFilePlanningResult",
    "FrontendGlobalSymbolPlanner",
    "FrontendLoweringResult",
    "FrontendSkeletonLowerer",
    "FrontendSymbolPlanningResult",
    "GlobalFilePlanner",
    "GlobalSymbolPlanner",
    "Model",
    "ModuleSkeletonLowerer",
    "ModuleSkeletonResult",
    "ProjectInitializationResult",
    "ProjectInitializer",
    "ProjectBuilder",
    "ProjectBuildResult",
    "PageLayoutComponentPass",
    "REQUIREMENT_UI_SCOPE_SCHEMA",
    "ResolvedVisualReference",
    "RequirementUIScopePass",
    "RequirementUIScopeResult",
    "SymbolPlanningResult",
    "StructuredModel",
    "TypeLowerer",
    "TypeLoweringResult",
    "VisualModel",
    "VisualModelConfigurationError",
    "VisualReferenceAnalysisResult",
    "VisualReferenceAnalyzer",
    "VisualReferenceResolutionResult",
    "VisualReferenceResolver",
    "VisualStructuredModel",
    "frontend_design_traceability",
    "finalize_frontend_design",
    "validate_frontend_design_minimum",
]
