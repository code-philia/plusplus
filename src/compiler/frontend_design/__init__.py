"""Technology-neutral frontend design, without review or validation passes."""

from .stage import FrontendIRGenerationPass, FrontendIRGenerationResult
from .context import frontend_ir_traceability, ui_data_associations

__all__ = ["FrontendIRGenerationPass", "FrontendIRGenerationResult",
           "frontend_ir_traceability", "ui_data_associations"]
