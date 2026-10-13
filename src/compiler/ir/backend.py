"""Canonical backend symbol table; module identities and graph edges are compiler-owned."""
from typing import Literal, TypedDict


class BackendTypeDefinition(TypedDict):
    name: str
    schema: str  # JSON Schema; references use #/types/Name.


class ModuleBase(TypedDict):
    id: str
    reqs: list[str]
    kind: Literal['API', 'FUNC', 'DB']
    name: str
    spec: str
    shared: bool
    shared_reason: str
    callees: list[str]
    callers: list[str]


class APIModule(ModuleBase):
    method: Literal['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']
    path: str
    payload: str
    response: str


class FunctionModule(ModuleBase):
    input: str
    output: str


class DatabaseModule(FunctionModule):
    read: str  # JSON array of entity or entity.field references.
    write: str


class BackendIR(TypedDict):
    ir_kind: Literal['backend_design']
    types: list[BackendTypeDefinition]
    modules: list[APIModule | FunctionModule | DatabaseModule]
