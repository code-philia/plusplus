from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class JsonModel(Protocol):
    """Minimal model interface required by a bounded structured agent."""

    def generate_json(
        self,
        *,
        schema_name: str,
        instructions: str,
        input_payload: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> dict[str, Any]: ...


@dataclass(slots=True)
class AgentInvocationResult:
    output: dict[str, Any] | None
    attempts: int
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.output is not None and not self.errors


class BaseStructuredAgent:
    """Run one schema-constrained model decision with bounded repair retries.

    This base module deliberately provides no shell, filesystem, network, test,
    or patch tools. Concrete agents receive compiler-built context and return a
    validated decision; deterministic callers own all side effects.
    """

    def __init__(
        self,
        model: JsonModel,
        *,
        schema_name: str,
        instructions: str,
        output_schema: dict[str, Any],
        retries: int = 2,
        trace: Callable[[str], None] | None = None,
    ) -> None:
        self._model = model
        self._schema_name = schema_name
        self._instructions = instructions
        self._output_schema = copy.deepcopy(output_schema)
        self._retries = max(0, min(int(retries), 5))
        self._trace = trace

    def invoke(
        self,
        input_payload: dict[str, Any],
        *,
        validate: Callable[[dict[str, Any]], list[str]],
    ) -> AgentInvocationResult:
        feedback: list[str] = []
        last_errors: list[str] = []
        for attempt in range(1, self._retries + 2):
            payload = copy.deepcopy(input_payload)
            if feedback:
                payload["agent_validation_feedback"] = feedback
            requirement_id = str(payload.get("requirement_id", ""))
            mode = str(payload.get("implementation_mode", ""))
            iteration = str(payload.get("iteration", ""))
            self._emit(
                f"MODEL_REQUEST requirement={requirement_id} mode={mode} "
                f"iteration={iteration} attempt={attempt}/{self._retries + 1}"
            )
            self._emit(
                _context_audit(
                    requirement_id=requirement_id,
                    mode=mode,
                    iteration=iteration,
                    attempt=attempt,
                    instructions=self._instructions,
                    input_payload=payload,
                    output_schema=self._output_schema,
                )
            )
            try:
                output = self._model.generate_json(
                    schema_name=self._schema_name,
                    instructions=self._instructions,
                    input_payload=payload,
                    output_schema=self._output_schema,
                )
            except Exception as exc:
                last_errors = [f"AGENT_MODEL_FAILED: {_describe_error(exc)}"]
                feedback = last_errors
                self._emit(last_errors[0])
                continue
            validation_errors = validate(output)
            if not validation_errors:
                self._emit(f"MODEL_ACCEPTED attempt={attempt}")
                return AgentInvocationResult(output=output, attempts=attempt)
            last_errors = list(dict.fromkeys(validation_errors))
            feedback = [
                "Repair only the structured output. Do not broaden scope or change the task.",
                *last_errors,
            ]
            self._emit("MODEL_REJECTED " + "; ".join(last_errors))
        return AgentInvocationResult(
            output=None,
            attempts=self._retries + 1,
            errors=last_errors or ["AGENT_MODEL_FAILED: no valid output was produced."],
        )

    def _emit(self, message: str) -> None:
        if self._trace is not None:
            self._trace(message)


def _describe_error(error: BaseException) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(parts) < 6:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {str(current).strip() or repr(current)}")
        next_error = current.__cause__
        if next_error is None and not current.__suppress_context__:
            next_error = current.__context__
        current = next_error
    return " <- ".join(parts)


def _context_audit(
    *,
    requirement_id: str,
    mode: str,
    iteration: str,
    attempt: int,
    instructions: str,
    input_payload: dict[str, Any],
    output_schema: dict[str, Any],
) -> str:
    def size(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    section_sizes = sorted(
        ((str(key), size(value)) for key, value in input_payload.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    envelope = {
        "instructions": instructions,
        "input_payload": input_payload,
        "output_schema": output_schema,
    }
    return (
        f"CONTEXT_AUDIT requirement={requirement_id} phase=implementation mode={mode} "
        f"iteration={iteration} attempt={attempt} context_total_chars={size(envelope)} "
        f"instructions_chars={size(instructions)} input_payload_chars={size(input_payload)} "
        f"output_schema_chars={size(output_schema)} section_chars="
        + ",".join(f"{key}:{value}" for key, value in section_sizes)
    )
