"""Explicit query -> authorize -> read -> select -> assemble orchestration."""
from __future__ import annotations
from .file_catalog import FileCatalog
from .edit_policy import EditPolicy
from .context_assembler import ContextAssembler
from .context_types import ImplementationContext


class FileContext:
    def __init__(self, root, requirements, database, backend, frontend,
                 backend_plan, frontend_plan, database_plan):
        self.files = FileCatalog(root, requirements, database, backend, frontend,
                                 backend_plan, frontend_plan, database_plan)
        self.policy = EditPolicy()
        self.assembler = ContextAssembler(self.files, self.policy)

    def build(self, rid, phase, session, test_targets, evidence) -> ImplementationContext:
        direct = self.files.direct_files(rid)
        source_paths = self.policy.source_paths(direct, session.active, session.released,
                                               self.files.recovery.get(rid, {}), phase)
        failing_tests, regression_owners = self.files.failed_test_context(evidence)
        visible_tests = {path for path in set(test_targets) | failing_tests if self.policy.phase_path(path, phase)}
        writable_paths = self.policy.authorize(source_paths | visible_tests, self.files.records, phase, test_targets)
        records = self.assembler.read(source_paths | visible_tests)
        records, context_state = session.select(records, visible_tests, direct)
        source_paths &= records.keys()
        shared, related = self.files.discover(rid, exclude=source_paths | visible_tests,
            include=lambda path: self.policy.discovery_path(path, phase))
        return self.assembler.assemble(rid=rid, phase=phase, session=session, test_targets=test_targets,
            evidence=evidence, records=records, writable_paths=writable_paths, context_state=context_state,
            source_paths=source_paths, visible_tests=visible_tests, direct=direct, shared=shared, related=related,
            failing_tests=failing_tests, regression_owners=regression_owners)
