"""Requirement associations for the canonical backend blueprint."""
from typing import Any

def design_traceability(design_ir: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    modules = {row['id']: row for row in design_ir.get('modules', [])}
    result = {}
    for row in design_ir.get('requirements', []):
        rid = row['requirement_id']
        result[rid] = {'api_ids': list(row.get('api_ids', [])),
                       'module_ids': sorted(key for key, module in modules.items()
                                            if rid in module['requirement_ids'])}
    return result

