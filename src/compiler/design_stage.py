"""Compatibility imports for the replacement backend design generator.

The previous requirement/API/recursive decomposition implementation was removed.
"""
from typing import Any
from .backend_design import BackendDesignPass as DesignPass, BackendDesignResult as DesignPassResult, project_design_context


def design_traceability(design_ir: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    modules = {row['id']: row for row in design_ir.get('modules', [])}
    result = {}
    for row in design_ir.get('requirements', []):
        rid = row.get('requirement_id', row.get('id', ''))
        result[rid] = {'api_ids': list(row.get('api_ids', [])),
                       'module_ids': sorted(key for key, module in modules.items()
                                            if rid in module.get('requirement_ids', [module.get('owner_requirement')]))}
    return result


__all__ = ['DesignPass', 'DesignPassResult', 'design_traceability', 'project_design_context']
