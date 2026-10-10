"""Optional image-analysis contracts, independent of frontend IR."""
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

class VisualReferenceErrorCode(StrEnum):
    """Diagnostic codes for optional visual-reference resolution and analysis."""

    IR_INVALID = "ARC4101"
    SYMBOL_DUPLICATE = "ARC4102"

    VISUAL_PATH_INVALID = "ARC4110"
    VISUAL_PATH_ESCAPE = "ARC4111"
    VISUAL_NOT_FOUND = "ARC4112"
    VISUAL_MEDIA_UNSUPPORTED = "ARC4113"
    VISUAL_TOO_LARGE = "ARC4114"
    VISUAL_READ_FAILED = "ARC4115"
    VISUAL_MODEL_UNAVAILABLE = "ARC4116"
    VISUAL_ANALYSIS_FAILED = "ARC4117"
    VISUAL_ANALYSIS_INVALID = "ARC4118"


@dataclass(slots=True)
class VisualReferenceIssue:
    code: VisualReferenceErrorCode
    message: str
    phase: str
    blame_symbol: str
    severity: str = "ERROR"
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.code.name

    def format(self) -> str:
        return f"{self.code.value} {self.title}: {self.message}"

def _string_list(*, max_items: int = 64) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": max_items,
        "items": {"type": "string", "minLength": 1},
    }


VISUAL_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "reference_id",
        "regions",
        "visible_controls",
        "layout_cues",
        "style_cues",
        "text_cues",
    ],
    "properties": {
        "reference_id": {"type": "string", "pattern": r"^VISUAL\.[0-9a-f]{16}$"},
        "regions": _string_list(),
        "visible_controls": _string_list(),
        "layout_cues": _string_list(),
        "style_cues": _string_list(),
        "text_cues": _string_list(),
    },
}

