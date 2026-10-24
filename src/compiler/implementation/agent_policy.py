"""Implementation permissions, context updates and acceptance transactions."""
from __future__ import annotations

from agent.files import FileInvocation, FileTransaction, source_versions
from agent.runtime import AgentRuntime
from agent.native import native_instructions
from compiler.context.model_calls import invoke_model
from model.client import describe_model_error
from .contracts import PROMPTS, SCHEMA
from .presentation import implementation_metadata
from .test_runner import passed


class ImplementationAgentPolicy:
    context_budget_message = 'File loading rounds exhausted; choose minimal necessary files'

    def __init__(self, *, model, root, context, logger, phase, requirement_id, session,
                 targets, evidence, snapshots, gate, call_started, validate_tests, blocked_error, attempts):
        self.model, self.root, self.file_context, self.log = model, root, context, logger
        self.phase, self.rid, self.session, self.targets = phase, requirement_id, session, targets
        self.evidence, self.snapshots, self.gate = evidence, snapshots, gate
        self.call_started, self.validate_tests, self.blocked_error = call_started, validate_tests, blocked_error
        self.attempts = attempts

    def begin_attempt(self, attempt):
        self.log.info(f'{self.phase} attempt {attempt}/{self.attempts}', node_id=self.rid)

    def prepare(self, feedback, rejected_files):
        payload = self.file_context.build(self.rid, self.phase, self.session, self.targets, self.evidence)
        versions = source_versions(payload['editable_files'])
        if feedback:
            payload['validation_feedback'] = feedback
            payload['rejected_patch'] = {'installed': False, 'files': rejected_files}
        return FileInvocation(payload, versions)

    def invoke(self, invocation):
        self.call_number = self.call_started()
        return invoke_model(self.model, implementation_metadata(self.phase), schema_name='tdd_' + self.phase,
            instructions=PROMPTS[self.phase], input_payload=invocation.payload, output_schema=SCHEMA)

    def invoke_native(self, invocation, *, tools, messages):
        self.call_number = self.call_started()
        return invoke_model(self.model, implementation_metadata(self.phase), method='generate_tool_turn',
            schema_name='tdd_' + self.phase, instructions=native_instructions(PROMPTS[self.phase]),
            input_payload=invocation.payload, tools=tools, messages=messages)

    def validate_context(self, invocation, arguments):
        self.reads = self.file_context.files.resolve_requests(arguments['request_files'], self.targets)
        self.releases = self.file_context.files.resolve_requests(arguments['release_files'], self.targets)
        disallowed = {path for path in self.reads if not self.file_context.policy.phase_path(path, self.phase)}
        if disallowed:
            raise ValueError(f'Files outside {self.phase} scope: {sorted(disallowed)}')

    def context(self, invocation, arguments):
        self.session.apply(arguments, self.reads, self.releases,
                           set(invocation.payload['context_state']['pinned_files']), self.call_number)
        self.log.info('Context read: ' + ', '.join(sorted(self.reads)) + '; release: ' +
                      ', '.join(sorted(self.releases)), node_id=self.rid)
        return {'read_paths': sorted(self.reads), 'released_paths': sorted(self.releases)}

    def propose(self, invocation, edits, *, check_cancelled):
        with FileTransaction(self.root) as transaction:
            changed = transaction.apply(edits, set(invocation.payload['editable_files']),
                expected_versions=invocation.versions, ignore_unchanged=self.phase == 'test_generation')
            tests = [path for path in changed
                     if self.file_context.files.records.get(path, {}).get('kind') == 'acceptance_test']
            if tests:
                self.validate_tests(tests)
            gate = self.gate()
            if not passed(gate):
                if any(row.get('failure_kind') == 'infrastructure' for row in gate if row['status'] != 'PASSED'):
                    raise self.blocked_error('Compilation executor unavailable; preserving previously accepted source: ' + str(gate))
                raise ValueError('Compilation/test-source gate failed: ' + str(gate))
            check_cancelled()
            for path, content in changed.items():
                self.snapshots.setdefault(path, content)
            self.session.touch(changed)
            transaction.accept()
        return None

    def reject(self, error, invocation, raw):
        if isinstance(error, self.blocked_error) or invocation is None:
            raise error
        feedback = [describe_model_error(error)]
        self.session.focus.update(path for path in AgentRuntime.rejected_files(raw)
                                  if path in invocation.payload['editable_files'])
        self.log.warning(f'{self.phase} rejected: ' + '; '.join(feedback), node_id=self.rid)
        return feedback
