"""Compatibility import; new callers use model.schema_validation."""
from model.schema_validation import shape_errors

__all__ = ["shape_errors"]
