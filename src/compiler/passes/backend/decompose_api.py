from __future__ import annotations

from .base import BackendDesignPass, BackendPassContext, BackendPassSpec
from .semantic import (
    API_DECOMPOSITION_INSTRUCTIONS,
    API_DECOMPOSITION_SCHEMA,
    _allocation_output_schema,
    _api_plan_issues,
    _api_template,
    _existing_api_contracts,
    _materialize_apis,
)


class DecomposeAPIPass(BackendDesignPass):
    spec = BackendPassSpec("decompose_api", "api_decomposition", "contract", "api")

    def run(self, module, context: BackendPassContext | None = None):
        context = super().run(module, context)
        if context.state is None or context.contract is None:
            raise ValueError("DecomposeAPIPass requires contract and state")
        compiler = context.compiler
        existing = _existing_api_contracts(context.state)
        payload = {
            "requirement": context.requirement,
            "fixed_requirement_contract": context.contract,
            "existing_api_contracts": existing,
            "fixed_template": _api_template(),
        }
        if context.upstream_feedback:
            payload["upstream_validation_feedback"] = context.upstream_feedback
        result = compiler._decision(
            phase="requirement_api",
            unit_id=context.requirement_id,
            schema_name="arc_requirement_api",
            instructions=API_DECOMPOSITION_INSTRUCTIONS,
            output_schema=_allocation_output_schema(
                API_DECOMPOSITION_SCHEMA, context.contract.get("obligations", [])
            ),
            context=payload,
            validator=lambda value: _api_plan_issues(
                value, context.requirement_id, context.contract, existing
            ),
        )
        if result.value is None:
            context.issues = result.issues
            return context
        for decided in result.value.get("modules", []):
            compiler._trace(
                "API_REUSE_DECISION "
                f"requirement={context.requirement_id} api={decided.get('name', '')} "
                f"decision={decided.get('reuse_decision', '')} "
                f"target={decided.get('reuse_target_module_id') or '-'}"
            )
        trial = context.state.clone()
        api_ids, issues = _materialize_apis(
            trial, context.requirement_id, context.contract, result.value
        )
        context.trial = trial
        context.last_trial = trial
        context.api_ids = api_ids
        context.decision = result.value
        context.issues = issues
        return context


__all__ = ["DecomposeAPIPass"]
