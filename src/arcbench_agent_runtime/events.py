from __future__ import annotations

import time

from .context import RuntimePaths
from .jsonio import append_jsonl


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


class EventClient:
    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths

    def _emit_runner_state(self, state: str, message: str) -> None:
        append_jsonl(
            self.paths.runner_events_path,
            {
                "type": "runner_state",
                "state": state,
                "timestamp": utc_timestamp(),
                "message": message,
            },
        )

    def mark_run_started(self, message: str) -> None:
        self._emit_runner_state("running", message)

    def mark_run_completed(self, message: str) -> None:
        self._emit_runner_state("completed", message)

    def mark_run_failed(self, message: str) -> None:
        self._emit_runner_state("failed", message)

    def notify_traceability_changed(self, reason: str) -> None:
        append_jsonl(
            self.paths.runner_events_path,
            {
                "type": "signal",
                "reason": reason,
                "timestamp": utc_timestamp(),
                "refresh": {
                    "submission": True,
                    "logs": False,
                    "commit_history": False,
                    "traceability_selected": True,
                    "traceability_all": True,
                    "preview": False,
                },
            },
        )
