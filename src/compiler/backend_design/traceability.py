"""Requirement associations for the canonical backend blueprint."""
from typing import Any

def design_traceability(design_ir: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    modules = {row['id']: row for row in design_ir.get('modules', [])}
    result = {}
    for rid in sorted({rid for module in modules.values() for rid in module['requirement_ids']}):
        result[rid] = {'api_ids': sorted(key for key, module in modules.items()
                                        if module['kind'] == 'API' and rid in module['requirement_ids']),
                       'module_ids': sorted(key for key, module in modules.items()
                                            if rid in module['requirement_ids'])}
    return result
