"""Frontend Design IR lowering exports."""

from .passes import FrontendLoweringPass
from ..canonical import CanonicalDesignLowering

__all__ = ["CanonicalDesignLowering", "FrontendLoweringPass"]
