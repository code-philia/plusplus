"""Database decision adapter: normalization, diagnostics and semantic validation."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from compiler.context.model_calls import invoke_model
from .presentation import database_metadata

from model.client import describe_model_error
from model.structured_task import StructuredTask, TaskRejected
from utils.trace import format_payload_trace
from .common import format_error
from .decision_normalization import normalize_decision_context


@dataclass(frozen=True, slots=True)
class DatabaseDecisionInput:
    phase: str
    schema_name: str
    schema: dict[str, Any]
    instructions: str
    node_id: str
    context: dict[str, Any]


class DatabaseDecisionTask(StructuredTask):
    def __init__(self, inputs: DatabaseDecisionInput, *, model, attempts, prepare, trace,
                 errors, validate: Callable | None):
        super().__init__(attempts, inputs.schema)
        self.inputs, self.model, self.prepare, self.trace = inputs, model, prepare, trace
        self.errors, self.validate = errors, validate
        self.attempt, self.started_at, self.notes = 0, 0.0, []

    def request(self, attempt, feedback, previous):
        self.attempt = attempt
        inputs = self.inputs
        payload, self.started_at = self.prepare(phase=inputs.phase, schema_name=inputs.schema_name,
            output_schema=inputs.schema, instructions=inputs.instructions, node_id=inputs.node_id,
            context=inputs.context, feedback=feedback, attempt=attempt - 1)
        return payload

    def invoke(self, request):
        inputs = self.inputs
        output = invoke_model(self.model, database_metadata(inputs.phase), schema_name=inputs.schema_name,
            instructions=inputs.instructions, input_payload=request, output_schema=inputs.schema)
        self.trace(self.event('MODEL_OUTPUT') + '\n' + format_payload_trace(output))
        return output

    def normalize(self, output):
        output, self.notes = normalize_decision_context(self.inputs.phase, output, self.inputs.context)
        return output

    def validate_shape(self, output):
        errors = super().validate_shape(output)
        if errors:
            raise TaskRejected(['Invalid design response: ' + '; '.join(errors)])
        return []

    def accept(self, output):
        errors = self.validate(output) if self.validate else []
        if errors:
            raise TaskRejected(errors)
        for note in self.notes:
            self.trace(f'MODEL_NORMALIZED phase={self.inputs.phase} requirement={self.inputs.node_id} action={note}')
        self.trace(self.event('MODEL_ACCEPTED'))
        return output

    def call_error(self, error):
        feedback = [f'Model call failed: {describe_model_error(error)}']
        self.trace(self.event('MODEL_ERROR') + f' error={feedback[0]}')
        return feedback

    def event(self, name):
        elapsed = round((time.monotonic() - self.started_at) * 1000)
        return (f'{name} phase={self.inputs.phase} requirement={self.inputs.node_id} '
                f'attempt={self.attempt} duration_ms={elapsed}')

    def rejected(self, feedback):
        self.trace(f'MODEL_REJECTED phase={self.inputs.phase} requirement={self.inputs.node_id} '
                   f'errors={"; ".join(feedback)}')

    def exhausted(self, feedback):
        self.errors.append(format_error('ARC2103', f'{self.inputs.phase} failed: {"; ".join(feedback)}',
                                        node_id=self.inputs.node_id))
        return None
