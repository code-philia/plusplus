from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .document import RequirementDocument


@dataclass(slots=True)
class PreprocessingResult:
    requirement_ir: dict[str, Any]
    dependency_graph: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class RequirementPreprocessor:
    """Parse the requirement metamodel and derive compiler-owned tree scheduling."""

    def compile(self, requirement_path: Path) -> PreprocessingResult:
        # ======================================================================
        #                             Preprocessing
        # ======================================================================
        # =================== Parse the requirement document ===================
        source_path = requirement_path.expanduser().resolve()
        try:
            source_bytes = source_path.read_bytes()
            text = source_bytes.decode("utf-8-sig")
            if source_path.suffix.lower() == '.md':
                blocks = re.findall(r'^```ya?ml\s*\n(.*?)^```\s*$', text, re.MULTILINE | re.DOTALL)
                if len(blocks) != 1:
                    raise ValueError('Markdown requirements must contain exactly one YAML code block')
                text = blocks[0]
            payload = yaml.safe_load(text)
        except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
            return PreprocessingResult({}, {}, [format_error('ARC1001',
                f'Cannot parse UTF-8 requirement document: {exc}', source=str(source_path))])
        # =========== Normalize identities, images and typed references =========
        document = RequirementDocument(source_path.name)
        requirements = document.normalize(payload)
        requirements['source'] = {'document': source_path.name,
                                  'sha256': hashlib.sha256(source_bytes).hexdigest()}
        # ============= Derive structure-only scheduling artifacts =============
        nodes = requirements['nodes']
        requirement_dependencies = {rid: list(node['children_ids']) for rid, node in nodes.items()}
        atomic_ids = requirements['atomic_units']
        def descendants(rid, active):
            if rid in active:
                return set()
            children = nodes[rid]['children_ids']
            return {child for child in children if child in atomic_ids} | set().union(
                *(descendants(child, active | {rid}) for child in children))
        atomic_dependencies = {rid: sorted(descendants(rid, set()) - {rid}) for rid in atomic_ids}
        graph = {'requirement_dependencies': requirement_dependencies,
                 'atomic_dependencies': atomic_dependencies,
                 'implementation_waves': self.topological_waves(list(nodes), requirement_dependencies, document.errors),
                 'atomic_implementation_waves': self.topological_waves(atomic_ids, atomic_dependencies, document.errors)}
        return PreprocessingResult(requirements, graph, document.errors)

    @staticmethod
    def topological_waves(requirement_ids, dependencies, errors):
        remaining, completed, waves = set(requirement_ids), set(), []
        while remaining:
            wave = sorted(rid for rid in remaining if set(dependencies[rid]) <= completed)
            if not wave:
                errors.append(format_error('ARC1303', 'Requirement tree contains a cycle'))
                break
            waves.append(wave)
            completed.update(wave)
            remaining.difference_update(wave)
        return waves


def format_error(code: str, message: str, *, node_id: str | None = None, source: str | None = None) -> str:
    context = ", ".join(value for value in (node_id, source) if value)
    return f"{code}: {message}" + (f" ({context})" if context else "")
