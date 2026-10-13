"""Requirement implementation with application build feedback and no tests."""
from __future__ import annotations

import os

from model.structured import BaseStructuredAgent
from compiler.implementation.agents.implementation import (
    ImplementationAgent, IMPLEMENTATION_INSTRUCTIONS, IMPLEMENTATION_SCOPE_SCHEMA,
)
from compiler.implementation.stage import NodeTDDOrchestrator, TDDStageResult


class CompilationRepairAgent(ImplementationAgent):
    def __init__(self, model, root, *, trace=None, model_log=None) -> None:
        super().__init__(model, root, retries=0, trace=trace, model_log=model_log)
        self._allowed_kinds = None
        self._scope_mode = "repair"
        self._agent = BaseStructuredAgent(
            model, schema_name="arc_compilation_repair", output_schema=IMPLEMENTATION_SCOPE_SCHEMA,
            instructions=IMPLEMENTATION_INSTRUCTIONS + """
This is a compile-only ablation. No tests are supplied, generated or executed.
implementation_feedback contains application compiler/build errors and prior patch rejection details.
Repair the reported compilation errors across application layers. Preserve requirement behavior,
public contracts, existing implementations and all unrelated requirements.
Do not suppress TypeScript checks, relax strictness, replace typed code with any/ts-ignore,
remove build commands, or delete behavior merely to pass compilation. Correct the source instead.
Use file_catalog and request_files to read additional source when the error originates elsewhere.
Only application source and the explicitly authorized project configuration can be edited.
""", retries=0, trace=trace, model_log=model_log, agent_name="CompilationRepairAgent",
        )


class CompileOnlyOrchestrator(NodeTDDOrchestrator):
    def __init__(self, model, output_root, **kwargs) -> None:
        super().__init__(model, output_root, **kwargs)
        # A typed contract change can require coordinated edits on both sides
        # of an API. Keep phase targets, but allow related application files
        # in the same compilation-checked patch instead of rejecting each half.
        self.implementation_agent._scope_mode = "repair"
        self.frontend_implementation_agent._scope_mode = "repair"
        try:
            self.max_iterations = max(1, min(int(os.environ.get("ARC_COMPILE_MAX_ITERATIONS", "5")), 50))
        except ValueError:
            self.max_iterations = 5
        self.compilation_agent = CompilationRepairAgent(model, self.output_root,
            trace=self._trace_implementation, model_log=self._write_implementation_model_log)

    def _implement_stage(self, stage, requirement_ids, allowed_kinds, agent) -> TDDStageResult:
        result = TDDStageResult(stage, status="IMPLEMENTED")
        for rid in requirement_ids:
            targets = [row for row in self._owned_targets(rid) if row.get("kind") in allowed_kinds]
            if not targets:
                continue
            for iteration in range(1, self.max_iterations + 1):
                changed, error = self._apply_edit(rid, self._requirement(rid), agent, result,
                    target_ids=tuple(row["module_id"] for row in targets), iteration=iteration)
                if changed:
                    break
                result.errors.append(f"{rid}: compilation implementation attempt {iteration}: {error}")
            else:
                result.status = "IMPLEMENTATION_FAILED"
                result.failed_requirements.append(rid)
        return result

    def repair_compilation(self, rid: str) -> TDDStageResult:
        result = TDDStageResult("application compilation", status="IMPLEMENTED")
        errors = self.validate_final()
        for iteration in range(1, self.max_iterations + 1):
            if not errors:
                return result
            result.errors.extend(errors)
            _, rejection = self._apply_edit(rid, self._requirement(rid), self.compilation_agent, result,
                                             iteration=iteration)
            if rejection:
                result.errors.append(rejection)
            errors = self.validate_final()
        if errors:
            result.status = "IMPLEMENTATION_FAILED"
            result.failed_requirements = [rid]
            result.errors.extend(errors)
        return result

    def run_test_layers(self, *args, **kwargs):
        raise RuntimeError("Test execution is disabled in compile-only mode")
