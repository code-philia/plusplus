"""Formal typed Design IR graph."""

from .backend import ApiIR, BackendDesignIR, BackendModuleIR, DatabaseOperationIR, FunctionIR
from .canonical import build_design_graph
from .contract import CallEdge, ContainEdge, DataFlowEdge, InvokeEdge, ReadEdge, RequirementTrace, WriteEdge
from .database import EntityIR, FieldIR, RelationIR
from .frontend import ComponentIR, EffectIR, EventIR, FrontendDesignIR, HandlerIR, PageIR, UIIR
from .module import DesignIR, DesignModule, DesignNode
from .projection import project_api_contract, project_api_contracts, project_api_modules, project_backend_module

__all__ = [
    "ApiIR", "BackendDesignIR", "BackendModuleIR", "CallEdge", "ComponentIR", "ContainEdge", "DataFlowEdge", "DatabaseOperationIR",
    "DesignIR", "DesignModule", "DesignNode", "EffectIR", "FrontendDesignIR", "EntityIR", "EventIR", "FieldIR", "FunctionIR",
    "HandlerIR", "InvokeEdge", "PageIR", "ReadEdge", "RelationIR", "RequirementTrace", "UIIR", "WriteEdge",
    "build_design_graph", "project_api_contract", "project_api_contracts", "project_api_modules", "project_backend_module",
]
