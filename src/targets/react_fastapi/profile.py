from __future__ import annotations

from dataclasses import dataclass


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


REACT_FASTAPI = TargetProfile(
    name="react-fastapi",
    backend_language="python",
    frontend_language="typescript",
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
