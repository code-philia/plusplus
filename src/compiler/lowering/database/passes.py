from __future__ import annotations

from ..base import LoweringPass, LoweringPassSpec
from ..context import LoweringContext


class DatabaseLoweringPass(LoweringPass):
    spec = LoweringPassSpec("database")
    kinds = frozenset(['entity', 'field', 'relation', 'database_operation'])

    def run(self, context: LoweringContext) -> LoweringContext:
        super().run(context)
        context.lowerer.lower_nodes(context.design, context.implementation, set(self.kinds))
        context.results[self.spec.name]["node_count"] = sum(
            1 for node in context.design.nodes.values()
            if str(node.kind).split(".")[-1].lower() in self.kinds
        )
        return context


__all__ = ["DatabaseLoweringPass"]
