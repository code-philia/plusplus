"""One bounded model/action/tool loop, with stage-owned execution policies."""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4
from utils.jsonio import write_json_atomic

from .tools import (CONTEXT_FILES, PROPOSE_EDITS, IndeterminateToolCall, JsonActionAdapter,
                    ToolCall, ToolRegistry)


class RuntimeStatus(str, Enum):
    ACCEPTED = 'ACCEPTED'
    EXHAUSTED = 'EXHAUSTED'
    BLOCKED = 'BLOCKED'
    CANCELLED = 'CANCELLED'
    FAILED = 'FAILED'
    INDETERMINATE = 'INDETERMINATE'


@dataclass(frozen=True, slots=True)
class RuntimeBudgets:
    attempts: int
    context_rounds: int
    context_per_attempt: bool = False

    def __post_init__(self):
        if self.attempts < 1 or self.context_rounds < 0:
            raise ValueError('Invalid agent execution budgets')

    @property
    def max_calls(self):
        return self.attempts + self.context_rounds * (self.attempts if self.context_per_attempt else 1)


@dataclass
class RuntimeResult:
    status: RuntimeStatus
    attempts: int = 0
    calls: int = 0
    feedback: list[str] = field(default_factory=list)
    value: Any = None
    trace: list[dict[str, Any]] = field(default_factory=list)


class AgentPolicy(Protocol):
    context_budget_message: str

    def begin_attempt(self, attempt: int) -> None: ...
    def prepare(self, feedback: list[str], rejected_files: list[str]) -> Any: ...
    def invoke(self, invocation: Any) -> Any: ...
    def validate_context(self, invocation: Any, arguments: dict) -> None: ...
    def context(self, invocation: Any, arguments: dict) -> Any: ...
    def propose(self, invocation: Any, edits: list[dict], *, check_cancelled) -> Any: ...
    def reject(self, error: Exception, invocation: Any, raw: Any) -> list[str]: ...


class AgentRuntime:
    def __init__(self, policy: AgentPolicy, *, adapter: JsonActionAdapter, budgets: RuntimeBudgets,
                 cancelled=lambda: False):
        self.policy, self.adapter, self.budgets, self.cancelled = policy, adapter, budgets, cancelled
        self.result = RuntimeResult(RuntimeStatus.FAILED)
        self.run_id, self.started = uuid4().hex, False
        self.registry = ToolRegistry()
        self.registry.register(CONTEXT_FILES, lambda arguments: policy.context(self.invocation, arguments))
        self.registry.register(PROPOSE_EDITS, lambda arguments: policy.propose(
            self.invocation, arguments['edits'], check_cancelled=self.check_cancelled))
        self.invocation = None

    def check_cancelled(self):
        if self.cancelled():
            raise asyncio.CancelledError

    def run(self) -> RuntimeResult:
        if self.started:
            raise RuntimeError('AgentRuntime instances execute once')
        self.started = True
        feedback, rejected_files, context_rounds = [], [], 0
        try:
            for attempt in range(1, self.budgets.attempts + 1):
                self.result.attempts = attempt
                self.check_cancelled()
                self.policy.begin_attempt(attempt)
                if self.budgets.context_per_attempt:
                    context_rounds = 0
                while self.result.calls < self.budgets.max_calls:
                    raw, action, event = None, None, None
                    self.invocation = None
                    try:
                        self.check_cancelled()
                        self.invocation = self.policy.prepare(feedback, rejected_files)
                        self.result.calls += 1
                        call_id = f'{self.run_id}:{self.result.calls}'
                        event = {'call_id': call_id, 'attempt': attempt, 'status': 'RUNNING'}
                        self.result.trace.append(event)
                        raw = self.policy.invoke(self.invocation)
                        event['raw_response'] = raw
                        self.check_cancelled()
                        action = self.adapter.parse(raw)
                        event['action'] = {'kind': action.kind, 'arguments': action.arguments,
                                           'blocked_reason': action.blocked_reason}
                        if action.kind == 'blocked':
                            event['status'] = 'BLOCKED'
                            raise ValueError(action.blocked_reason)
                        if action.kind == 'context_files':
                            self.policy.validate_context(self.invocation, action.arguments)
                            context_rounds += 1
                            if context_rounds > self.budgets.context_rounds:
                                raise ValueError(self.policy.context_budget_message)
                        self.check_cancelled()
                        tool = self.registry.dispatch(ToolCall(call_id, action.kind, action.arguments))
                        event['tool'] = {'state': tool.state.value, 'effects': tool.effects,
                                         'value': tool.value,
                                         'error': str(tool.error) if tool.error else None}
                        value = tool.require_value()
                        event['status'] = 'ACCEPTED' if action.kind == 'propose_edits' else 'CONTEXT_UPDATED'
                        if action.kind == 'propose_edits':
                            self.result.status, self.result.value = RuntimeStatus.ACCEPTED, value
                            self.result.feedback = feedback
                            return self.result
                    except Exception as error:
                        if event is not None:
                            if event['status'] != 'BLOCKED':
                                event['status'] = 'REJECTED'
                            event['error'] = str(error)
                        if isinstance(error, (IndeterminateToolCall, TypeError, AttributeError, NameError,
                                              KeyError, AssertionError, NotImplementedError)):
                            raise
                        feedback = self.policy.reject(error, self.invocation, raw)
                        rejected_files = self.rejected_files(raw)
                        self.result.feedback = feedback
                        break
            self.result.status = (RuntimeStatus.BLOCKED if self.result.trace and
                self.result.trace[-1]['status'] == 'BLOCKED' else RuntimeStatus.EXHAUSTED)
            return self.result
        except asyncio.CancelledError:
            self.result.status = RuntimeStatus.CANCELLED
            self.settle_trace('CANCELLED')
            raise
        except BaseException as error:
            self.result.status = (RuntimeStatus.INDETERMINATE if isinstance(error, IndeterminateToolCall)
                                  else RuntimeStatus.FAILED)
            self.settle_trace(self.result.status.value)
            raise

    def settle_trace(self, status):
        if self.result.trace and self.result.trace[-1]['status'] in {'RUNNING', 'REJECTED'}:
            self.result.trace[-1]['status'] = status

    def report(self):
        return {'run_id': self.run_id, 'status': self.result.status.value,
                'attempts': self.result.attempts, 'model_calls': self.result.calls,
                'max_model_calls': self.budgets.max_calls,
                'feedback': self.result.feedback, 'trace': self.result.trace}

    def save_trace(self, path):
        try:
            write_json_atomic(path, self.report())
        except (OSError, TypeError, ValueError) as error:
            print(f'AGENT_TRACE_WRITE_FAILED path={path}: {error}', file=sys.stderr)

    @staticmethod
    def rejected_files(raw):
        edits = raw.get('edits', []) if isinstance(raw, dict) else []
        return sorted({edit['file'] for edit in edits if isinstance(edit, dict)
                       and isinstance(edit.get('file'), str)}) if isinstance(edits, list) else []
