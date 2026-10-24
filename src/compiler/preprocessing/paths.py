"""Resolve requirement inputs shared by the CLI and compiler tools."""
from pathlib import Path


def locate_requirement_file(input_path: str) -> str:
    path = Path(input_path).expanduser().resolve()
    if path.is_dir():
        path = next((path / name for name in ('requirements.yaml', 'requirements.yml')
                     if (path / name).is_file()), path)
    if not path.is_file() or path.suffix not in ('.yaml', '.yml'):
        raise ValueError(f'Requirements YAML not found: {path}')
    return str(path)
