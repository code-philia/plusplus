"""Static database lowering after project initialization, without fixtures or execution."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arcbench_agent_runtime.jsonio import write_json_atomic

from ..artifacts import CompilerArtifactStore
from ..database_design import validate_schema_ir
from ..file_planning import GlobalFilePlanner
from ..skeleton_lowering import TypeLowerer
from ..symbol_planning import GlobalSymbolPlanner
from .strategy import get_strategy


@dataclass(slots=True)
class DatabaseLoweringResult:
    manifest: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class DatabaseLowerer:
    """Compile database-neutral tables; use registries solely for backend bindings."""

    def lower(self, schema: dict[str, Any], project_manifest: dict[str, Any], output_root: Path, *, dialect: str = "sqlite") -> DatabaseLoweringResult:
        result = DatabaseLoweringResult()
        try:
            if project_manifest.get('status') != 'PROJECT_INITIALIZED':
                raise ValueError('Project initialization must complete before database lowering')
            if schema.get('ir_kind') != 'database_schema':
                raise ValueError('Expected database-neutral Schema IR; dialect-specific schemas are unsupported')
            if schema.get('status') != 'RESOLVED':
                raise ValueError('Database lowering requires a RESOLVED Schema IR')
            validation_errors = validate_schema_ir(schema)
            if validation_errors:
                result.errors.extend(validation_errors)
                return result
            strategy = get_strategy(dialect)
            if strategy.dialect == 'sqlite' and project_manifest.get('profile') != 'web-react18-tailwind4-express-drizzle-sqlite':
                raise ValueError('SQLite lowering requires the initialized SQLite/Drizzle project profile')
            # Binding names/paths use the same frozen allocation policy as later
            # backend planning. SQL does not depend on the compatibility views.
            projected = {'entities': [
                {'key': table['name'], 'fields': [
                    {'name': column['name'], 'type': column['type'], 'nullable': column['nullable']}
                    for column in table['columns']
                ]}
                for table in schema['tables']
            ]}
            design = {'requirements': [], 'modules': []}
            symbols = GlobalSymbolPlanner().plan(design, projected, project_manifest)
            if not symbols.ok:
                result.errors.extend(symbols.errors)
                return result
            files = GlobalFilePlanner(output_root).plan(design, symbols.registry, project_manifest)
            if not files.ok:
                result.errors.extend(files.errors)
                return result
            types = TypeLowerer().lower(symbols.registry, files.registry)
            if not types.ok:
                result.errors.extend(types.errors)
                return result
            lowered = strategy.lower(schema, symbols.registry, files.registry)
            sources = {**types.sources, **lowered.sources}
            result.warnings.extend(lowered.warnings)
            result.warnings.extend(f'DATABASE_RUNTIME_RULE: {rule["scope"]}: {rule["kind"]}' for rule in schema.get('runtime_rules', []))
            result.sources = sources
            result.manifest = {
                'schema_version': 5, 'status': 'DATABASE_SCHEMA_LOWERED',
                'dialect': strategy.dialect, 'orm': 'drizzle',
                'schema_sha256': hashlib.sha256(json.dumps(schema, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                'tables': lowered.tables,
                'initialization': {'mode': 'create_if_not_exists', 'statements': lowered.statements,
                                   'entrypoint': 'backend/src/db/init.ts', 'executed': False},
                'ddl': {'sqlite': 'backend/sql/schema.sqlite.sql'},
                'policies': lowered.policies,
                'runtime_rules': copy.deepcopy(schema.get('runtime_rules', [])),
                'generated_files': sorted(path for path in sources if path.startswith('backend/src/')),
                'source_files': sorted(sources),
            }
        except (AttributeError, KeyError, TypeError, ValueError, StopIteration) as exc:
            result.errors.append(f'DATABASE_LOWERING_INVALID: {exc}')
            result.sources.clear()
        return result


def lower_database(output_root: Path, store: CompilerArtifactStore, project_manifest: dict[str, Any], *, schema_path: Path | None = None, dialect: str = "sqlite") -> DatabaseLoweringResult:
    """Read the committed schema.json and write sources; never open a database."""
    output_root = output_root.expanduser().resolve()
    result = DatabaseLoweringResult()
    try:
        if store.root.parent != output_root:
            raise ValueError('Artifact store and initialized project output roots differ')
        if not (output_root / 'backend/package.json').is_file():
            raise ValueError('Initialized backend/package.json is missing')
        path = schema_path or store.database_root / 'schema.json'
        schema = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(schema, dict):
            raise ValueError('schema.json must contain a JSON object')
        result = DatabaseLowerer().lower(schema, project_manifest, output_root, dialect=dialect)
        if result.ok:
            result.artifacts.update(store.write_generated_sources(result.sources))
    except (OSError, ValueError) as exc:
        result.errors.append(f'DATABASE_LOWERING_FAILED: {exc}')
    report_root = store.lowering_root / 'database'
    write_json_atomic(report_root / 'report.json', {
        'status': 'LOWERED' if result.ok else 'FAILED',
        'errors': result.errors, 'warnings': result.warnings,
        'files': sorted(result.sources),
    })
    result.artifacts['database_lowering_report'] = str(report_root / 'report.json')
    if result.ok:
        write_json_atomic(report_root / 'manifest.json', result.manifest)
        result.artifacts['database_lowering_manifest'] = str(report_root / 'manifest.json')
    return result
