"""Design adapter for the common structured decision lifecycle."""
from __future__ import annotations

import copy

from model.structured_task import StructuredTask
from .model_calls import invoke_model


class DesignDecision(StructuredTask):
    def __init__(self, *, attempts, schema, model, metadata, instructions, payload,
                 accept, logger, requirement_id, label, on_attempt, failure_prefix,
                 invoke=None):
        super().__init__(attempts, schema)
        self.model, self.metadata, self.instructions = model, metadata, instructions
        self.payload, self.evaluate = payload, accept
        self.logger, self.requirement_id, self.label = logger, requirement_id, label
        self.on_attempt, self.failure_prefix, self.call = on_attempt, failure_prefix, invoke

    def request(self, attempt, feedback, previous):
        self.on_attempt()
        self.logger.info(f'{self.label} design attempt {attempt}/{self.attempts}',
                         node_id=self.requirement_id)
        request = copy.deepcopy(self.payload)
        if feedback:
            request['validation_feedback'], request['previous_output'] = feedback, previous
        return request

    def invoke(self, request):
        if self.call is not None:
            return self.call(request)
        return invoke_model(self.model, self.metadata, schema_name=self.metadata.operation,
                            instructions=self.instructions, input_payload=request, output_schema=self.schema)

    def accept(self, output):
        return self.evaluate(output)

    def rejected(self, feedback):
        self.logger.warning(f'{self.label} validation rejected: ' + '; '.join(feedback),
                            node_id=self.requirement_id)

    def exhausted(self, feedback):
        raise ValueError(self.failure_prefix + '; '.join(feedback))
