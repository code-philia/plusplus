"""Canonical deterministic projections of the aggregate Backend Design IR."""

from __future__ import annotations

import copy
from typing import Any


def project_api_contracts(design_ir: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the shared API Interface without Backend invocation details."""

    return [
        project_api_contract(module)
        for module in design_ir.get("modules", [])
        if isinstance(module, dict) and module.get("kind") == "API"
    ]


def project_api_modules(design_ir: dict[str, Any]) -> list[dict[str, Any]]:
    """Return Backend-only API invocation graph records."""

    return [
        project_api_module(module)
        for module in design_ir.get("modules", [])
        if isinstance(module, dict) and module.get("kind") == "API"
    ]


def project_backend_module(module: dict[str, Any]) -> dict[str, Any]:
    """Return the complete compact representation used by FUNC/DB artifacts."""

    return {**project_api_contract(module), **project_api_module(module)}


def project_api_contract(module: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(module.get("id", "")),
        "spec": str(module.get("spec", "")),
        "inputs": [
            _compact_field(item)
            for item in module.get("inputs", [])
            if isinstance(item, dict)
        ],
        "outputs": [
            _compact_field(item)
            for item in module.get("outputs", [])
            if isinstance(item, dict)
        ],
        "effects": [
            _compact_effect(item)
            for item in module.get("effects", [])
            if isinstance(item, dict)
        ],
    }


def project_api_module(module: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(module.get("id", "")),
        "callees": list(
            dict.fromkeys(str(value) for value in module.get("callees", []) if str(value))
        ),
    }


def _compact_field(field: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(field[key])
        for key in ("semantic_id", "name", "type", "required")
        if key in field
    }


def _compact_effect(effect: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(effect.get(key))
        for key in ("id", "operation", "target", "fields")
    }
