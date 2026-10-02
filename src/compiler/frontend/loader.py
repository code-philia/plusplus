from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class LoadedRequirementDocument:
    path: Path
    payload: Any
    raw_bytes: bytes
    sha256: str


class RequirementLoadError(ValueError):
    """Raised when a requirement source cannot be located or decoded."""


class RequirementLoader:
    """Load YAML or JSON requirement documents from a file or directory."""

    CANDIDATES = ("requirements.yaml", "requirements.yml", "requirements.json")

    def resolve(self, source: str | Path) -> Path:
        path = Path(source).expanduser().resolve()
        if path.is_dir():
            for name in self.CANDIDATES:
                candidate = path / name
                if candidate.is_file():
                    return candidate
            raise RequirementLoadError(
                f"No requirement document found in {path}; expected one of {', '.join(self.CANDIDATES)}"
            )
        if not path.is_file():
            raise RequirementLoadError(f"Requirement source does not exist: {path}")
        if path.suffix.lower() not in {".yaml", ".yml", ".json"}:
            raise RequirementLoadError(f"Unsupported requirement document extension: {path.suffix or '<none>'}")
        return path

    def load(self, source: str | Path) -> LoadedRequirementDocument:
        path = self.resolve(source)
        try:
            raw_bytes = path.read_bytes()
            text = raw_bytes.decode("utf-8-sig")
            if path.suffix.lower() == ".json":
                payload = json.loads(text)
            else:
                payload = yaml.safe_load(text)
        except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise RequirementLoadError(f"Cannot parse requirement document {path}: {exc}") from exc
        return LoadedRequirementDocument(
            path=path,
            payload=payload if payload is not None else {},
            raw_bytes=raw_bytes,
            sha256=hashlib.sha256(raw_bytes).hexdigest(),
        )


def load_requirement(source: str | Path) -> LoadedRequirementDocument:
    return RequirementLoader().load(source)


__all__ = ["LoadedRequirementDocument", "RequirementLoadError", "RequirementLoader", "load_requirement"]