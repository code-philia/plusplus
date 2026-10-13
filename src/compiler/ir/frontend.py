"""Framework-neutral UI tree, typed state and event-handler contracts."""
from typing import Literal, TypedDict
from .backend import BackendTypeDefinition

UIKind = Literal['app', 'page', 'region', 'form', 'list', 'item', 'input', 'button',
                 'text', 'image', 'dialog', 'navigation', 'other']


class UINode(TypedDict):
    id: str
    name: str
    kind: UIKind
    description: str
    reqs: list[str]
    children: list['UINode']
    data: list[str]
    events: list[str]


class FrontendData(TypedDict):
    id: str
    reqs: list[str]
    name: str
    owner: str
    spec: str
    schema: str
    initial: str


class FrontendEvent(TypedDict):
    id: str
    reqs: list[str]
    ui: str
    name: str
    trigger: str
    handler: str


class FrontendHandler(TypedDict):
    id: str
    reqs: list[str]
    name: str
    spec: str
    input: str
    output: str
    read: list[str]
    write: list[str]
    apis: list[str]


class FrontendIR(TypedDict):
    ir_kind: Literal['frontend_design']
    ui: UINode
    data: list[FrontendData]
    events: list[FrontendEvent]
    handlers: list[FrontendHandler]
    types: list[BackendTypeDefinition]


class PageReferenceState(TypedDict):
    reference: str
    sha256: str
    analysis: str


class PageState(TypedDict):
    name: str
    ui_id: str
    references: list[PageReferenceState]
