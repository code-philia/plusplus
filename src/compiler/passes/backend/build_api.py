from __future__ import annotations

from .base import BackendDesignPass, BackendPassContext, BackendPassSpec
from .semantic import (
    _complete_constraint_read_effects,
    _existing_api_contracts,
    _model_design_context,
    _normalize_database_backed_session_effects,
    _normalize_requirement_contract,
    _requirement_contract_issues,
    REQUIREMENT_CONTRACT_INSTRUCTIONS,
    REQUIREMENT_CONTRACT_SCHEMA,
    _requirement_contract_template,
)


class BuildAPIPass(BackendDesignPass):
    spec = BackendPassSpec("build_api", "requirement_contract", "requirement", "contract")

    def run(self, module, context: BackendPassContext | None = None):
        context = super().run(module, context)
        if context.state is None:
            raise ValueError("BuildAPIPass requires DesignState")
        compiler = context.compiler
        contract_result = compiler._decision(
            phase="requirement_contract",
            unit_id=context.requirement_id,
            schema_name="arc_requirement_contract",
            instructions=REQUIREMENT_CONTRACT_INSTRUCTIONS,
            output_schema=REQUIREMENT_CONTRACT_SCHEMA,
            context={
                "requirement": context.requirement,
                "design_context": _model_design_context(context.design_context),
                "existing_api_contracts": _existing_api_contracts(context.state),
                "fixed_template": _requirement_contract_template(context.requirement_id),
            },
            validator=lambda value: _requirement_contract_issues(
                value, context.requirement_id, context.design_context, context.requirement
            ),
        )
        if contract_result.value is None:
            context.issues = contract_result.issues
            context.retry = False
            return context
        contract = _normalize_requirement_contract(contract_result.value)
        contract, normalized = _normalize_database_backed_session_effects(
            contract, design_context=context.design_context
        )
        if normalized:
            compiler._trace(
                "CONTRACT_EFFECTS_NORMALIZED "
                f"requirement={context.requirement_id} effects={normalized} "
                "from=SESSION_WRITE to=CREATE source=database_entities"
            )
        contract, completed = _complete_constraint_read_effects(
            contract, requirement_id=context.requirement_id,
            design_context=context.design_context,
        )
        if completed:
            compiler._trace(
                "CONTRACT_EFFECTS_COMPLETED "
                f"requirement={context.requirement_id} effects={completed} "
                "source=database_unique_constraints"
            )
        context.contract = contract
        context.state = context.state.clone()
        context.state.requirement_contracts[context.requirement_id] = contract
        return context


__all__ = ["BuildAPIPass"]
