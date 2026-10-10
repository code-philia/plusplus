"""Unresolved product capabilities shared by backend and frontend design."""
from typing import TypedDict


class PendingCapability(TypedDict):
    id: str
    requirement_ids: list[str]
    capability: str
    reason: str
    # Empty until frontend behavior using this capability has been designed.
    function_ids: list[str]
