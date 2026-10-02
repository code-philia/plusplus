from __future__ import annotations

from enum import StrEnum
from typing import TypeAlias


JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


class IRLevel(StrEnum):
    REQUIREMENT = "requirement"
    DESIGN = "design"
    IMPLEMENTATION = "implementation"


class NodeKind(StrEnum):
    REQUIREMENT = "requirement"
    ENTITY = "entity"
    FIELD = "field"
    RELATION = "relation"
    API = "api"
    FUNCTION = "function"
    DATABASE_OPERATION = "database_operation"
    PAGE = "page"
    COMPONENT = "component"
    UI = "ui"
    EVENT = "event"
    HANDLER = "handler"
    EFFECT = "effect"
    FILE = "file"
    SYMBOL = "symbol"
    TYPE = "type"
    IMPORT = "import"


__all__ = ["IRLevel", "JSONScalar", "JSONValue", "NodeKind"]