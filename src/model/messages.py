"""Shared model message and context-segment serialization."""
from __future__ import annotations

import json
from typing import Any

from .prompt_context import render_section, stage_sections

CONTEXT_SEGMENTS_KEY = "context_segments"


def user_messages(input_payload: dict[str, Any], *, schema_name: str | None = None) -> list[dict[str, str]]:
    """Render named sections, preserving compiler payloads and explicit segments."""

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
                    "content": render_section(str(segment.get("name", f"segment_{index}")),
                                              str(segment.get("purpose", "")), payload),
                }
            )
    if rendered:
        return rendered

    markdown_context = input_payload.get("context_markdown")
    if set(input_payload) == {"context_markdown"} and isinstance(markdown_context, str):
        return [{"role": "user", "content": markdown_context}]
    if schema_name:
        return [{"role": "user", "content": render_section(section["name"], section["purpose"], section["payload"])}
                for section in stage_sections(schema_name, input_payload)]
    user_input = json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))
    return [{"role": "user", "content": user_input}]
