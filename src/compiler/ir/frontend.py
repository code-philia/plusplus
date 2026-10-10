"""Lightweight, technology-neutral UI and interaction blueprints.

Flat UI records form a tree through ordered children IDs. Components, framework
lifecycle hooks, DOM elements, HTTP endpoints and source files belong to lowering.
These TypedDicts describe JSON contracts, not runtime validation.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from .backend import BackendField, BackendType, BackendTypeDefinition
from .integration import PendingCapability


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


ValueSource = LiteralValue | ReferenceValue


class UIRepeat(TypedDict):
    source: ValueSource
    # Evaluated in this node's item context; must be stable and unique.
    key: ValueSource


class UINode(TypedDict):
    id: str
    # Semantic responsibility, independent of requirement IDs and framework names.
    name: str
    kind: Literal[
        "screen", "group", "text", "input", "choice", "action", "image",
        "list", "feedback",
        "form", "field", "navigation", "section", "dialog", "tabs", "tab",
        "table", "row", "cell", "heading", "link", "progress",
    ]
    spec: str
    requirement_ids: list[str]
    children: list[str]
    # Semantic properties: label, value, options, visible, enabled, etc.
    bindings: dict[str, ValueSource]
    events: list["UIEvent"]
    # Optional screen location; no router or URL serialization policy.
    route: NotRequired[str]
    # Visual intent, not CSS or framework properties.
    presentation: NotRequired[str]
    visual_reference_ids: NotRequired[list[str]]
    repeat: NotRequired[UIRepeat]


class FrontendState(TypedDict):
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
    arguments: dict[str, ValueSource]
    # Business conditions stay prose; evaluation is specified by the caller.
    condition: NotRequired[str]


class UIEvent(TypedDict):
    id: str
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


class RequirementUIAssociation(TypedDict):
    implements: list[str]
    uses: list[str]


class FrontendIR(TypedDict):
    ir_kind: Literal["frontend_design"]
    # Resolves API module IDs and named types; not a transport contract.
    backend_ref: str
    types: list[BackendTypeDefinition]
    root_ui_id: str
    ui: list[UINode]
    data: list[FrontendState]
    functions: list[UIFunction]
    visual_references: NotRequired[list[UIVisualReference]]
    pending_capabilities: NotRequired[list[PendingCapability]]
    requirement_links: NotRequired[dict[str, RequirementUIAssociation]]


def frontend_events(ir: dict) -> list[dict]:
    """Project nested events with their owner for lowering and source bindings."""
    return [{**event, "ui_id": ui["id"]}
            for ui in ir["ui"] for event in ui["events"]]


__all__ = [
    "FrontendIR", "UINode", "FrontendState", "UIEvent", "UIFunction", "UICall",
    "ValueSource", "LiteralValue", "ReferenceValue", "UIRepeat", "UIVisualReference",
    "RequirementUIAssociation",
    "frontend_events",
]
