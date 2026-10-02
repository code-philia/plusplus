from __future__ import annotations

from dataclasses import dataclass, field

from ...ir.design.module import DesignModule


@dataclass(slots=True)
class LinkingContext:
    graph: DesignModule
    results: dict[str, dict[str, object]] = field(default_factory=dict)


class LinkingPass:
    name = "linking"

    def run(self, context: LinkingContext) -> LinkingContext:
        context.results[self.name] = {"status": "COMPLETED"}
        return context


__all__ = ["LinkingContext", "LinkingPass"]
