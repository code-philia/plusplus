"""Backend Design IR lowering exports."""

from .passes import BackendLoweringPass
from ..canonical import CanonicalDesignLowering

__all__ = ["BackendLoweringPass", "CanonicalDesignLowering"]
