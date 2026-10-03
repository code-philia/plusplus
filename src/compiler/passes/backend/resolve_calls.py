from __future__ import annotations

from .base import BackendDesignPass, BackendPassContext, BackendPassSpec
from .semantic import (
    DesignPassResult,
    _behavioral_completeness_issues,
    _collapse_passthrough_modules,
)


class ResolveCallsPass(BackendDesignPass):
    spec = BackendPassSpec("resolve_calls", "resolve_calls", "module", "backend")

    def run(self, module, context: BackendPassContext | None = None):
        context = super().run(module, context)
        if context.state is None:
            raise ValueError("ResolveCallsPass requires DesignState")
        state, collapse_warnings = _collapse_passthrough_modules(context.state, context.requirement_id) if context.requirement_id else (context.state, [])
        design = state.to_ir()
        errors = [f"{issue.code}: {issue.message}" for issue in context.issues]
        warnings = list(context.warnings) + list(collapse_warnings)
        if not errors:
            warnings.extend(
                "DESIGN_VALIDATION_WARNING: " + issue.message
                for issue in _behavioral_completeness_issues(design)
            )
        context.state = state
        context.result = DesignPassResult(
            design, dict(context.node_states), errors, warnings
        )
        context.results[self.spec.name].update({"module_ids": sorted(state.modules)})
        return context


__all__ = ["ResolveCallsPass"]
