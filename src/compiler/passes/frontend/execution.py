from __future__ import annotations

from typing import Any


def run_frontend_phase(compiler: Any, phase: str, tasks: list[dict[str, Any]]) -> None:
    """Run exactly one frontend design phase over the prepared task list."""
    if phase == "ui":
        for task in tasks:
            if not compiler._requirement_task("ui", task):
                compiler.failed_ui.add(task["id"])
        return
    for task in tasks:
        rid = task["id"]
        if compiler.traceability[rid]["mode"] == "NONE":
            continue
        if rid in compiler.failed_ui:
            compiler.warnings.append(f"{rid}: UI task failed; {phase} uses the committed IR where available.")
        if phase == "behavior" and rid in compiler.failed_assemble:
            compiler.warnings.append(f"{rid}: assembly task failed; behavior uses the committed component structure.")
        if compiler.traceability[rid]["mode"] == "SUMMARY":
            if phase == "assemble":
                linked = compiler.traceability[rid].get("ui_ids", [])
                components = {compiler.workspace.owner(eid) for eid in linked}
                for child in compiler.nodes:
                    if compiler.nodes[child].get("parent_id") == rid:
                        components.update(compiler.traceability[child].get("component_ids", []))
                compiler.workspace.apply_requirement_batch(
                    {"creates": [], "updates": [], "associations": [{"id": cid} for cid in sorted(components)[:8]]},
                    [rid], set(), phase)
                compiler._refresh_traceability()
                continue
            if phase == "behavior" and not compiler._has_behavior_scope(rid):
                continue
        component_roots = {c["ui_root_id"] for c in compiler.workspace.tables["components"].values()}
        linked_ui = set(compiler.traceability[rid].get("ui_ids", [])) - component_roots
        if phase == "assemble" and not linked_ui:
            continue
        if phase == "behavior" and not compiler._has_behavior_scope(rid):
            continue
        if not compiler._requirement_task(phase, task) and phase == "assemble":
            compiler.failed_assemble.add(rid)

def run_frontend_data_phase(compiler: Any, tasks: list[dict[str, Any]]) -> None:
    """Finalize data bindings from the committed UI/data IR.

    Data entities are intentionally created by the UI contract pass. This
    pass only refreshes the derived associations consumed by behavior and
    traceability, so it cannot duplicate model calls or mutate ownership.
    """
    compiler._refresh_traceability()

__all__ = ["run_frontend_data_phase", "run_frontend_phase"]
