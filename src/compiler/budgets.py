"""Immutable execution limits, captured once after runtime environment loading."""
from __future__ import annotations

import os
from dataclasses import dataclass


def positive_limit(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class ExecutionBudgets:
    model_attempts: int = 5
    tdd_iterations: int = 5
    load_rounds: int = 12
    run_attempts: int = 2

    def __post_init__(self) -> None:
        if min(self.model_attempts, self.tdd_iterations, self.load_rounds, self.run_attempts) < 1:
            raise ValueError('Execution budgets must be positive')

    @classmethod
    def from_environment(cls) -> ExecutionBudgets:
        return cls(positive_limit('MODEL_ATTEMPT', 5), positive_limit('ARC_TDD_MAX_ITERATIONS', 5),
                   positive_limit('ARC_TDD_LOAD_ROUNDS', 12), positive_limit('ARC_TDD_RUN_ATTEMPTS', 2))
