"""Compatibility import for standalone lowering; command parsing lives in cli."""
from cli.stage_commands import lower_main as main

__all__ = ['main']
