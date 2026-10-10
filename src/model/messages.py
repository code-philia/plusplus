"""Shared model message and context-segment serialization."""
from __future__ import annotations

import json
from typing import Any

CONTEXT_SEGMENTS_KEY = "context_segments"


def user_messages(input_payload: dict[str, Any]) -> list[dict[str, str]]:
    """Render one user message per context segment, stable prefix first.

    A layered payload carries ``context_segments`` as an ordered list of
    ``{"name": str, "payload": dict}`` rows. Each row becomes its own user
    message so the provider can reuse the prefix cache for every segment that
    did not change between iterations. Any other payload keeps the historic
    single-message shape.
    """

    segments = input_payload.get(CONTEXT_SEGMENTS_KEY)
    rendered: list[dict[str, str]] = []
    if isinstance(segments, list) and segments:
        extra = {
            key: value
            for key, value in input_payload.items()
            if key != CONTEXT_SEGMENTS_KEY
        }
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict) or not isinstance(
                segment.get("payload"), dict
            ):
                rendered = []
                break
            payload = dict(segment["payload"])
            if extra and index == len(segments) - 1:
                payload.update(extra)
            rendered.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "context_segment": str(segment.get("name", f"segment_{index}")),
                            "context": payload,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )
    if rendered:
        return rendered

    markdown_context = input_payload.get("context_markdown")
    user_input = (
        markdown_context
        if set(input_payload) == {"context_markdown"} and isinstance(markdown_context, str)
        else json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))
    )
    return [{"role": "user", "content": user_input}]


