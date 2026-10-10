"""Lightweight, technology-neutral UI and interaction blueprints.

Flat UI records form a tree through ordered children IDs. Components, framework
lifecycle hooks, DOM elements, HTTP endpoints and source files belong to lowering.
These TypedDicts describe JSON contracts, not runtime validation.
See frontend.md for reference and execution semantics.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from .backend import BackendField, BackendType, BackendTypeDefinition


JSONValue = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]


class LiteralValue(TypedDict):
    kind: Literal["literal"]
    value: JSONValue


class ReferenceValue(TypedDict):
    # IDs identify data records, current input semantic_ids, local calls or
    # ancestor repeating UI nodes, respectively. Paths select object fields.
    kind: Literal["data", "input", "result", "item"]
    id: str
    path: NotRequired[list[str | int]]


UIValue = LiteralValue | ReferenceValue


class UIRepeat(TypedDict):
    source: UIValue
    # Evaluated in this node's item context; must be stable and unique.
    key: UIValue


class UINode(TypedDict):
    id: str
    kind: Literal[
        "screen", "group", "text", "input", "choice", "action", "image",
        "list", "feedback",
    ]
    spec: str
    requirement_ids: list[str]
    children: list[str]
    # Semantic properties: label, value, options, visible, enabled, etc.
    bindings: dict[str, UIValue]
    # Optional screen location; no router or URL serialization policy.
    route: NotRequired[str]
    # Visual intent, not CSS or framework properties.
    presentation: NotRequired[str]
    visual_reference_ids: NotRequired[list[str]]
    repeat: NotRequired[UIRepeat]


class UIData(TypedDict):
    id: str
    name: str
    type: BackendType
    nullable: bool
    spec: str
    requirement_ids: list[str]
    # null = application-wide; otherwise a UI subtree owns the state.
    scope_ui_id: str | None
    initial: NotRequired[JSONValue]


class UICall(TypedDict):
    id: str
    # Event handlers call FUNC only; functions may call FUNC or backend API.
    kind: Literal["FUNC", "API"]
    target_id: str
    # Keys are exact target input semantic_ids; missing optional inputs omitted.
    arguments: dict[str, UIValue]
    # Business conditions stay prose; evaluation is specified by the caller.
    condition: NotRequired[str]


class UIEvent(TypedDict):
    id: str
    ui_id: str
    # Semantic triggers, e.g. activate, change, submit, enter; not DOM names.
    name: str
    spec: str
    requirement_ids: list[str]
    payload: list[BackendField]
    handlers: list[UICall]


class UIFunction(TypedDict):
    id: str
    spec: str
    requirement_ids: list[str]
    inputs: list[BackendField]
    outputs: list[BackendField]
    reads: list[str]
    writes: list[str]
    calls: list[UICall]


class UIVisualReference(TypedDict):
    id: str
    path: str
    description: NotRequired[str]


class FrontendIR(TypedDict):
    ir_kind: Literal["frontend_design"]
    # Resolves API module IDs and named types; not a transport contract.
    backend_ref: str
    types: list[BackendTypeDefinition]
    root_ui_id: str
    ui: list[UINode]
    data: list[UIData]
    events: list[UIEvent]
    functions: list[UIFunction]
    visual_references: NotRequired[list[UIVisualReference]]


__all__ = [
    "FrontendIR", "UINode", "UIData", "UIEvent", "UIFunction", "UICall",
    "UIValue", "LiteralValue", "ReferenceValue", "UIRepeat", "UIVisualReference",
]
