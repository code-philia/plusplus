"""Persist each design model call, including provider fallback attempts."""
from __future__ import annotations

import re
import sys
import time
import uuid
import json
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable
from utils.logging import SynchronousLog

_active_log: ContextVar[tuple[Path, dict[str, Any]] | None] = ContextVar("design_model_log", default=None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save(path: Path, record: dict[str, Any]) -> None:
    """Keep each actual request and its raw reply together in one text log."""
    try:
        attempts = record["attempts"]
        if not attempts and record["status"] == "RUNNING":
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        for number, attempt in enumerate(attempts or [{}], 1):
            attempt_path = path if number == 1 else path.with_name(
                f"{path.stem}.attempt-{number:02d}.log"
            )
            prefix = f"[{attempt.get('started_at', record['started_at'])}] [{record['schema_name']}]"
            with attempt_path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(f"{prefix} MODEL_CALL model={record['model']} attempt={number} "
                             f"status={attempt.get('status', record['status'])}\n")
                if "request" in attempt:
                    handle.write(f"{prefix} MODEL_INPUT\n")
                    for message in attempt["request"].get("messages", []):
                        handle.write(f"\n--- {message.get('role', 'message')} ---\n")
                        content = message.get("content")
                        if isinstance(content, str):
                            handle.write(content)
                        elif isinstance(content, list):
                            for part in content:
                                if part.get("type") == "text":
                                    handle.write(part["text"])
                                elif part.get("type") == "image_url":
                                    handle.write("\n--- image_url ---\n")
                                    handle.write(part["image_url"]["url"])
                if "output" in attempt:
                    handle.write(f"\n\n{prefix} MODEL_OUTPUT\n")
                    handle.write(attempt["output"] or "")
                if attempt.get('usage') is not None:
                    handle.write(f"\n\n{prefix} MODEL_USAGE " + json.dumps(attempt['usage'], ensure_ascii=False, default=str))
                if attempt.get("reasoning") is not None:
                    handle.write(f"\n\n{prefix} MODEL_REASONING\n")
                    handle.write(attempt["reasoning"])
                if "error" in attempt:
                    handle.write(f"\n\n{prefix} MODEL_ERROR "
                                 f"{attempt['error']['type']}: {attempt['error']['message']}\n")
                if "finished_at" in attempt:
                    handle.write(f"\n\n[{attempt['finished_at']}] [{record['schema_name']}] "
                                 f"MODEL_FINISHED duration_ms={attempt['duration_ms']}\n")
                if number == len(attempts or [{}]) and "error" in record:
                    handle.write(f"\n{prefix} MODEL_CALL_ERROR "
                                 f"{record['error']['type']}: {record['error']['message']}\n")
    except (OSError, TypeError, ValueError) as exc:
        print(f"MODEL_LOG_WRITE_FAILED path={path}: {exc}", file=sys.stderr)


def log_design_call(method: Callable[..., Any]) -> Callable[..., Any]:
    """Log logical calls independently of their negotiated transport format."""
    @wraps(method)
    def wrapped(self: Any, **kwargs: Any) -> Any:
        schema_name = kwargs.get("schema_name", "")
        usage_path = getattr(self, "_usage_path", None)
        is_design = schema_name.startswith(("arc_database_", "arc_backend_", "backend_", "frontend_", "tdd_", "stage_repair")) or schema_name == "arc_visual_reference_analysis"
        if usage_path is None or not is_design:
            return method(self, **kwargs)
        call_id = uuid.uuid4().hex
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        name = re.sub(r"[^A-Za-z0-9_-]", "_", schema_name)
        path = Path(usage_path).parent / "model_logs" / f"{timestamp}_{name}_{call_id}.log"
        record = {
            "call_id": call_id, "started_at": _now(), "status": "RUNNING",
            "operation": method.__name__, "schema_name": schema_name,
            "model": self.model, "attempts": [],
        }
        _save(path, record)
        SynchronousLog('Model', workspace_root=Path(usage_path).parent.parent).info(
            f"MODEL_LOG schema_name={schema_name} call_id={call_id} path={path}")
        token = _active_log.set((path, record))
        started = time.monotonic()
        try:
            output = method(self, **kwargs)
            record.update(status="SUCCEEDED")
            return output
        except Exception as exc:
            record.update(status="FAILED", error={"type": type(exc).__name__, "message": str(exc)})
            raise
        finally:
            record.update(finished_at=_now(), duration_ms=int((time.monotonic() - started) * 1000),
                          output_mode=getattr(self, "structured_output_mode", None))
            _save(path, record)
            _active_log.reset(token)
    return wrapped


def logged_completion(client: Any, **request: Any) -> Any:
    """Capture actual messages and raw output even when JSON parsing later fails."""
    active = _active_log.get()
    if active is None:
        return client.chat.completions.create(**request)
    path, record = active
    attempt: dict[str, Any] = {"started_at": _now(), "status": "RUNNING", "request": request}
    record["attempts"].append(attempt)
    _save(path, record)
    started = time.monotonic()
    try:
        response = client.chat.completions.create(**request)
        message = response.choices[0].message
        attempt.update(status="SUCCEEDED", output=message.content,
                       reasoning=getattr(message, "reasoning_content", None))
        usage = getattr(response, 'usage', None)
        if usage is not None:
            attempt['usage'] = usage.model_dump() if hasattr(usage, 'model_dump') else usage
        return response
    except Exception as exc:
        attempt.update(status="FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        attempt.update(finished_at=_now(), duration_ms=int((time.monotonic() - started) * 1000))
        _save(path, record)
