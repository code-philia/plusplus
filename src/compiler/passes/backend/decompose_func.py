from __future__ import annotations

from .base import BackendDesignPass, BackendPassContext, BackendPassSpec
from .semantic import _behavioral_completeness_issues


class DecomposeFUNCPass(BackendDesignPass):
    spec = BackendPassSpec("decompose_func", "func_decomposition", "api", "module")

    def run(self, module, context: BackendPassContext | None = None):
        context = super().run(module, context)
        if context.trial is None or context.contract is None:
            raise ValueError("DecomposeFUNCPass requires materialized APIs")
        compiler = context.compiler
        failure = list(context.issues)
        if not failure:
            for api_id in context.api_ids:
                expanded, child_issues = compiler._expand_module(
                    context.trial,
                    context.requirement_id,
                    context.requirement,
                    api_id,
                    context.contract,
                    context.design_context,
                    depth=0,
                )
                if expanded is None:
                    failure = child_issues
                    break
                context.trial = expanded
        if not failure:
            completeness = _behavioral_completeness_issues(
                context.trial.to_ir(), requirement_ids={context.requirement_id}
            )
            for issue in completeness:
                issue.repair_action = "REOPEN_PARENT"
            failure = completeness
        context.issues = failure
        if not failure:
            context.state = context.trial
            context.completed = True
            context.retry = False
            return context
        context.retry = any(issue.repair_action == "REOPEN_PARENT" for issue in failure)
        if context.retry:
            context.upstream_feedback = [issue.feedback() for issue in failure]
        return context


__all__ = ["DecomposeFUNCPass"]
