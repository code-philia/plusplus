from .semantic import _behavioral_completeness_issues
from .base import BackendDesignPass, BackendPassSpec


class ResolveCallsPass(BackendDesignPass):
    spec = BackendPassSpec(name="resolve_calls", phase="resolve_calls",
                           input_level="module", output_level="backend")

    def run(self, module, context=None):
        context = super().run(module, context)
        edges = {
            item.id: sorted(set(item.callees))
            for item in module.modules.values()
            if item.callees
        }
        reverse: dict[str, list[str]] = {}
        for caller, callees in edges.items():
            for callee in callees:
                reverse.setdefault(callee, []).append(caller)
        for module_id, callers in reverse.items():
            if module_id in module.modules:
                module.modules[module_id].callers = sorted(set(callers))
        context.results[self.spec.name].update({
            "edges": edges,
            "resolved_targets": sorted(reverse),
        })
        completeness_issues = _behavioral_completeness_issues(module.to_dict())
        context.results[self.spec.name]["completeness_issues"] = [
            issue.message for issue in completeness_issues
        ]
        context.diagnostics.extend(
            "DESIGN_VALIDATION_WARNING: " + issue.message
            for issue in completeness_issues
        )
        return context


__all__ = ["ResolveCallsPass"]