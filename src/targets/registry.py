from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .react_fastapi import TargetProfile, load_target_profile


@dataclass(frozen=True, slots=True)
class TargetDefinition:
    """Lazy target services used by the compiler driver."""

    profile: TargetProfile

    def backend_source_pipeline(self) -> type[Any]:
        from .react_fastapi.lowering.backend.pipeline import BackendSourcePipeline
        return BackendSourcePipeline

    def database_source_pipeline(self) -> type[Any]:
        from .react_fastapi.lowering.database.pipeline import DatabaseSourcePipeline
        return DatabaseSourcePipeline

    def frontend_source_pipeline(self) -> type[Any]:
        from .react_fastapi.lowering.frontend.pipeline import FrontendSourcePipeline
        return FrontendSourcePipeline


def load_target_definition(name: str = "react-fastapi") -> TargetDefinition:
    return TargetDefinition(profile=load_target_profile(name))


__all__ = ["TargetDefinition", "load_target_definition"]
