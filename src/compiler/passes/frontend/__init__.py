"""Frontend Design IR generation passes."""

from .base import FrontendDesignPass, FrontendPassSpec
from .decompose_components import DecomposeComponentsPass
from .discover_pages import DiscoverPagesPass
from .infer_behavior import InferBehaviorPass
from .infer_data import InferDataPass
from .pipeline import FrontendIRGenerationPipeline

__all__ = [
    "DecomposeComponentsPass", "DiscoverPagesPass", "FrontendDesignPass", "FrontendIRGenerationPipeline",
    "FrontendPassSpec", "InferBehaviorPass", "InferDataPass",
]