"""One project-local store for visual analyses, indexed by image paths."""
from __future__ import annotations

import copy
import json
import posixpath
from pathlib import Path
from typing import Any, Sequence

from utils.jsonio import write_json_atomic


def cache_path(artifact_root: Path) -> Path:
    return artifact_root / "design" / "visual_cache.json"


def image_key(path: str) -> str:
    return posixpath.normpath(path.replace(chr(92), "/"))


def read_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Visual cache must be a path-indexed object: {path}")
    return {image_key(key): row for key, row in value.items() if isinstance(row, dict)}


def consolidate_cache(path: Path) -> None:
    legacy = path.parent / "frontend" / "visual_cache.json"
    if legacy.is_file():
        # Prefer newer canonical records when both locations contain a path.
        write_json_atomic(path, {**read_cache(legacy), **read_cache(path)})
        legacy.unlink()


def persist_records(records: Sequence[dict[str, Any]], path: Path) -> None:
    cached = read_cache(path)
    for source in records:
        if not source.get("analysis"):
            continue
        record = copy.deepcopy(source)
        keys = [image_key(key) for key in [record["source_path"], *record.get("aliases", [])]]
        for key in keys:
            previous = cached.get(key, {})
            if previous.get("sha256") == record.get("sha256"):
                record["requirement_ids"] = sorted(set(record.get("requirement_ids", []))
                                                   | set(previous.get("requirement_ids", [])))
        for key in keys:
            cached[key] = record
    if records:
        write_json_atomic(path, cached)
