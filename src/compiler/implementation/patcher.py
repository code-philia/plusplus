"""Public compatibility exports; all file transaction logic lives in agent.files."""
from agent.files import apply_edits, read_source, restore, sha256, source_versions

__all__ = ['apply_edits', 'read_source', 'restore', 'sha256', 'source_versions']
