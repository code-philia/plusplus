"""Database design passes."""

from .base import DatabaseDesignPass, DatabasePassSpec
from .discover_entities import DiscoverEntitiesPass
from .infer_fields import InferFieldsPass
from .infer_relations import InferRelationsPass

__all__ = ["DatabaseDesignPass", "DatabasePassSpec", "DiscoverEntitiesPass", "InferFieldsPass", "InferRelationsPass"]