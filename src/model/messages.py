"""Shared model message and context-segment serialization."""
from __future__ import annotations

import json
from typing import Any

from .prompt_context import render_section, stage_sections

CONTEXT_SEGMENTS_KEY = "context_segments"

REQUIREMENT_FIDELITY = '''## Requirement fidelity
For design, test generation, implementation and repair, explicit requirement descriptions, scenarios
and applicable ancestor constraints define the observable product contract. Visual references, generated
IR, existing source and convenience choices must not override that contract.
Preserve explicitly specified UI wording and accessible names exactly, including spelling, case,
punctuation and dynamic naming patterns. Do not translate, paraphrase, abbreviate or substitute another
spelling unless the document explicitly permits it. Use the same names in design, source and tests.
Preserve explicitly specified UI roles and states. A required button is a button, not a link merely
because it navigates; fields need their specified associated labels. Implement the actual control
semantics, not just matching text or an aria-label on a different control. Use alternatives only when
the requirement explicitly allows them.
At each specified scenario step, required controls and navigation entries must exist, be visible and
be operable in the stated scope. Do not invent prerequisite expansion, hover or navigation steps that
hide an expected input or action. Preserve these contracts in shared forms and prerequisite flows too.
Tests must exercise the specified names, roles and states; do not broaden selectors or expected values
to accept an implementation that contradicts explicit requirements.
Where the requirement is silent, choose suitable wording, roles and interaction details consistent
with the supplied visual evidence and existing application conventions. Do not invent extra obligations.
'''


def system_instructions(instructions: str) -> str:
    """Apply the same requirement contract to normal and fallback model requests."""
    return REQUIREMENT_FIDELITY + '\n' + instructions


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
