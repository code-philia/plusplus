"""Structured decision lifecycle; business contexts and commits remain with callers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from .client import describe_model_error
from .schema_validation import shape_errors

Result = TypeVar('Result')


class TaskRejected(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__('; '.join(errors))


class StructuredTask(ABC, Generic[Result]):
    def __init__(self, attempts: int, schema: dict[str, Any]):
        if attempts < 1:
            raise ValueError('StructuredTask requires a positive attempt budget')
        self.attempts, self.schema = attempts, schema

    def run(self) -> Result:
        feedback, previous = [], None
        for attempt in range(1, self.attempts + 1):
            request = self.request(attempt, feedback, previous)
            try:
                output = self.invoke(request)
            except (TypeError, AttributeError, NameError, KeyError, AssertionError, NotImplementedError):
                raise
            except Exception as exc:
                feedback = self.call_error(exc)
            else:
                previous = output
                try:
                    output = self.normalize(output)
                    errors = self.validate_shape(output)
                    if errors:
                        raise TaskRejected(errors)
                    return self.accept(output)
                except TaskRejected as exc:
                    feedback = exc.errors
                except ValueError as exc:
                    feedback = [describe_model_error(exc)]
            self.rejected(feedback)
        return self.exhausted(feedback)

    def normalize(self, output: Any) -> Any:
        return output

    def validate_shape(self, output: Any) -> list[str]:
        return shape_errors(output, self.schema)

    def call_error(self, error: Exception) -> list[str]:
        return [describe_model_error(error)]

    @abstractmethod
    def request(self, attempt: int, feedback: list[str], previous: Any) -> Any: ...

    @abstractmethod
    def invoke(self, request: Any) -> Any: ...

    @abstractmethod
    def accept(self, output: Any) -> Result: ...

    @abstractmethod
    def rejected(self, feedback: list[str]) -> None: ...

    @abstractmethod
    def exhausted(self, feedback: list[str]) -> Result: ...
