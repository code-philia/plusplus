"""Gate-repair entrypoint; the shared runtime owns the only model/tool loop."""
from pathlib import Path
from agent.native import select_action_adapter

from agent.runtime import AgentRuntime, RuntimeBudgets, RuntimeStatus
from agent.tools import JsonActionAdapter, FILE_ACTION_INSTRUCTIONS
from compiler.budgets import ExecutionBudgets
from compiler.repair_agent import GateRepairPolicy
from utils.jsonio import write_json_atomic
from utils.logging import SynchronousLog


INSTRUCTIONS = '''## Background and role
You repair compilation of an initialized Express/React TypeScript project.
current_task identifies a lowering or build gate. gate_results contains the actual failure.
For lowering preserve minimal typed skeletons; do not implement business logic or weaken contracts.
For build preserve all implemented behavior, requirement contracts and acceptance tests.

## Context and edits
file_catalog provides available files; editable_files contains explicitly loaded current source.
Request exact catalog paths before inspecting/editing them. Do not invent source, disable
type checking, remove build commands, change dependency versions or modify tests/design artifacts.
validation_feedback describes the failure; rejected_patch lists the proposed files with installed:false.
The rejected reply's edits were all rolled back. Only the supplied current file contents reflect
installed code; earlier accepted changes remain installed. Submit coordinated fixes together.
If the cause cannot be repaired in the permitted source scope, report the blocker using the selected protocol.
''' + FILE_ACTION_INSTRUCTIONS


def repair_gate(model, output, stage, check, evidence, *, contracts=None, budgets=None):
    root = Path(output).resolve()
    if callable(getattr(model, 'set_usage_path', None)):
        model.set_usage_path(root / '.arc/model_usage.jsonl')
    artifact = root / '.arc/repairs' / stage
    limits = budgets if budgets is not None else ExecutionBudgets.from_environment()
    policy = GateRepairPolicy(model=model, root=root, stage=stage, check=check, evidence=evidence,
        contracts=contracts, instructions=INSTRUCTIONS, logger=SynchronousLog('StageRepair', workspace_root=root),
        attempts=limits.model_attempts)
    runtime = AgentRuntime(policy, adapter=select_action_adapter(model, json_adapter=JsonActionAdapter(strip_blocked=False,
        mixed_message='Read/release only catalog files without edits', empty_message='Provide a targeted source repair')),
        budgets=RuntimeBudgets(limits.model_attempts, 12, context_per_attempt=True))
    try:
        result = runtime.run()
    finally:
        runtime.save_trace(artifact / 'agent_trace.json')
    if result.status == RuntimeStatus.ACCEPTED:
        write_json_atomic(artifact / 'report.json', {'status': 'REPAIRED', 'attempts': result.attempts})
        return result.value
    write_json_atomic(artifact / 'report.json', {'status': 'FAILED', 'attempts': limits.model_attempts,
                                               'feedback': result.feedback})
    return {'ok': False, 'errors': result.feedback, 'gate_results': policy.evidence}
