"""Compatibility imports for the lightweight frontend design stage."""
from .frontend_design import (
    FrontendIRGenerationPass, FrontendIRGenerationResult,
    frontend_ir_traceability, ui_data_associations,
)

__all__ = ["FrontendIRGenerationPass", "FrontendIRGenerationResult",
           "frontend_ir_traceability", "ui_data_associations"]
