from __future__ import annotations

from dataclasses import dataclass, field

from .conventions import (
    BACKEND_FRAMEWORK,
    BACKEND_LANGUAGE,
    DATABASE_ORM,
    FRONTEND_FRAMEWORK,
    FRONTEND_LANGUAGE,
)
from .types import DEFAULT_TYPE_MAPPINGS


@dataclass(frozen=True, slots=True)
class TargetProfile:
    name: str
    backend_language: str
    frontend_language: str
    backend_root: str
    frontend_root: str
    shared_root: str
    backend_extension: str
    frontend_extension: str
    backend_framework: str = BACKEND_FRAMEWORK
    frontend_framework: str = FRONTEND_FRAMEWORK
    database_orm: str = DATABASE_ORM
    type_mappings: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TYPE_MAPPINGS))


REACT_FASTAPI = TargetProfile(
    name="react-fastapi",
    backend_language=BACKEND_LANGUAGE,
    frontend_language=FRONTEND_LANGUAGE,
    backend_root="backend/src",
    frontend_root="frontend/src",
    shared_root="shared/src",
    backend_extension=".py",
    frontend_extension=".tsx",
)


def load_target_profile(name: str = "react-fastapi") -> TargetProfile:
    if name != REACT_FASTAPI.name:
        raise ValueError(f"Unknown ARC target profile: {name}")
    return REACT_FASTAPI


__all__ = ["REACT_FASTAPI", "TargetProfile", "load_target_profile"]
