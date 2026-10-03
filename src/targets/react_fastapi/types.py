from __future__ import annotations

from typing import Final

DEFAULT_TYPE_MAPPINGS: Final[dict[str, str]] = {
    "semantic_string": "str",
    "semantic_integer": "int",
    "semantic_number": "float",
    "semantic_boolean": "bool",
    "semantic_datetime": "datetime",
    "semantic_uuid": "UUID",
}

__all__ = ["DEFAULT_TYPE_MAPPINGS"]
