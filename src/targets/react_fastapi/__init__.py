"""React/FastAPI target profile namespace."""

from .conventions import (
    BACKEND_FRAMEWORK,
    BACKEND_LANGUAGE,
    DATABASE_ORM,
    FRONTEND_FRAMEWORK,
    FRONTEND_LANGUAGE,
)
from .profile import REACT_FASTAPI, TargetProfile, load_target_profile
from .types import DEFAULT_TYPE_MAPPINGS

__all__ = [
    "BACKEND_FRAMEWORK",
    "BACKEND_LANGUAGE",
    "DATABASE_ORM",
    "DEFAULT_TYPE_MAPPINGS",
    "FRONTEND_FRAMEWORK",
    "FRONTEND_LANGUAGE",
    "REACT_FASTAPI",
    "TargetProfile",
    "load_target_profile",
]
