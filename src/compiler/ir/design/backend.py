from __future__ import annotations

from dataclasses import dataclass, field

from .module import DesignNode
from ..common import NodeKind, Reference


@dataclass(slots=True)
class ApiIR(DesignNode):
    method: str = "GET"
    path: str = "/"
    request_type: str | None = None
    response_type: str | None = None
    handler: Reference | None = None
    auth_required: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        self.kind = NodeKind.API


@dataclass(slots=True)
class FunctionIR(DesignNode):
    parameters: list[dict[str, str]] = field(default_factory=list)
    return_type: str = "None"
    calls: list[Reference] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.kind = NodeKind.FUNCTION


@dataclass(slots=True)
class DatabaseOperationIR(DesignNode):
    operation: str = "read"
    entity: Reference = field(default_factory=lambda: Reference(""))
    fields: list[Reference] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.kind = NodeKind.DATABASE_OPERATION



@dataclass(slots=True)
class BackendModuleIR:
    id: str
    kind: str
    name: str
    spec: str = ""
    inputs: list[dict[str, object]] = field(default_factory=list)
    outputs: list[dict[str, object]] = field(default_factory=list)
    effects: list[dict[str, object]] = field(default_factory=list)
    obligation_ids: list[str] = field(default_factory=list)
    owner_requirement: str | None = None
    parent_id: str | None = None
    callers: list[str] = field(default_factory=list)
    callees: list[str] = field(default_factory=list)
    attributes: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "BackendModuleIR":
        known = {"id", "kind", "name", "spec", "inputs", "outputs", "effects", "obligation_ids", "owner_requirement", "parent_id", "callers", "callees"}
        return cls(
            id=str(value.get("id", "")), kind=str(value.get("kind", "")), name=str(value.get("name", "")),
            spec=str(value.get("spec", "")), inputs=list(value.get("inputs", []) or []),
            outputs=list(value.get("outputs", []) or []), effects=list(value.get("effects", []) or []),
            obligation_ids=list(value.get("obligation_ids", []) or []), owner_requirement=value.get("owner_requirement"),
            parent_id=value.get("parent_id"), callers=list(value.get("callers", []) or []),
            callees=list(value.get("callees", []) or []), attributes={key: item for key, item in value.items() if key not in known},
        )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "id": self.id, "kind": self.kind, "name": self.name, "spec": self.spec,
            "inputs": list(self.inputs), "outputs": list(self.outputs), "effects": list(self.effects),
            "obligation_ids": list(self.obligation_ids), "owner_requirement": self.owner_requirement,
            "parent_id": self.parent_id, "callers": list(self.callers), "callees": list(self.callees),
        }
        result.update(self.attributes)
        return result


@dataclass(slots=True)
class BackendDesignIR:
    requirements: list[dict[str, object]] = field(default_factory=list)
    modules: dict[str, BackendModuleIR] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "BackendDesignIR":
        raw_modules = value.get("modules", [])
        modules = {
            str(item.get("id")): BackendModuleIR.from_dict(item)
            for item in raw_modules
            if isinstance(item, dict) and item.get("id")
        }
        return cls(requirements=list(value.get("requirements", []) or []), modules=modules)

    def to_dict(self) -> dict[str, object]:
        return {"requirements": list(self.requirements), "modules": [self.modules[key].to_dict() for key in sorted(self.modules)]}

    def modules_of_kind(self, kind: str) -> list[BackendModuleIR]:
        return [module for module in self.modules.values() if module.kind == kind]


__all__ = ["ApiIR", "BackendDesignIR", "BackendModuleIR", "DatabaseOperationIR", "FunctionIR"]