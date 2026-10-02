from __future__ import annotations

from ...ir.design.module import DesignModule
from .base import LinkingContext
from .link_backend_database import LinkBackendDatabasePass
from .link_frontend_backend import LinkFrontendBackendPass
from .link_requirements import LinkRequirementsPass


class LinkingPipeline:
    pass_pipeline = (LinkRequirementsPass, LinkFrontendBackendPass, LinkBackendDatabasePass)

    def run(self, graph: DesignModule) -> LinkingContext:
        context = LinkingContext(graph=graph)
        for pass_type in self.pass_pipeline:
            context = pass_type().run(context)
        return context


__all__ = ["LinkingPipeline"]
