"""Technology-neutral incremental backend blueprint generation."""
from .stage import BackendDesignPass, BackendDesignResult, project_design_context
from .validation import validate_backend_ir
from .traceability import design_traceability

__all__ = ['BackendDesignPass', 'BackendDesignResult', 'project_design_context', 'validate_backend_ir', 'design_traceability']
