from __future__ import annotations

import json
import os
from typing import Any


def full_payload_logging_enabled() -> bool:
    """Return whether full model payloads should be written to the debug log."""

    return os.getenv("ARC_DEBUG_FULL_PAYLOAD", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def format_payload_trace(payload: Any) -> str:
    if not full_payload_logging_enabled():
        return "[omitted; set ARC_DEBUG_FULL_PAYLOAD=1 to enable]"
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
