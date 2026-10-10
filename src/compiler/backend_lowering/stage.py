"""Plan module files and exported types, then emit signatures and server routes."""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.jsonio import write_json_atomic
from compiler.runtime.artifacts import CompilerArtifactStore
from .projection import project_lowering_design
from ..backend_design.validation import validate_backend_ir
from ..lowering.files import GlobalFilePlanner
from .skeletons import ModuleSkeletonLowerer
from ..lowering.types import TypeLowerer
from ..lowering.symbols import GlobalSymbolPlanner
from .glue import BackendGlueLowerer
from .database_bindings import render_bindings
from .module_types import attach_module_types
from ..database_lowering.sqlite import storage_type

PROFILE = 'web-react18-tailwind4-express-drizzle-sqlite'


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(slots=True)
class BackendLoweringResult:
    sources: dict[str, str] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)
    tables: dict[str, Any] = field(default_factory=dict)
    route_registry: dict[str, Any] = field(default_factory=dict)
    import_plan: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class BackendLowerer:
    """Plan module/type ownership before emitting unimplemented function frameworks."""

    def lower(self, blueprint: dict[str, Any], schema: dict[str, Any], project_manifest: dict[str, Any],
              database_manifest: dict[str, Any], output_root: Path, *, default_port: int = 3000) -> BackendLoweringResult:
        result = BackendLoweringResult()
        try:
            if project_manifest.get('status') != 'PROJECT_INITIALIZED' or project_manifest.get('profile') != PROFILE:
                raise ValueError('Backend lowering requires the initialized Express/Drizzle/SQLite project profile')
            if blueprint.get('ir_kind') != 'backend_design':
                raise ValueError('Expected the current canonical Backend IR')
            if database_manifest.get('dialect') != 'sqlite':
                raise ValueError('Backend profile requires SQLite database initialization')
            if database_manifest.get('status') != 'DATABASE_SCHEMA_LOWERED':
                raise ValueError('Database lowering must finish before backend lowering')
            scenarios = {key for row in blueprint['modules'] for criterion in row.get('acceptance_criteria', []) for key in criterion['scenario_ids']}
            problems = validate_backend_ir(blueprint, schema, scenarios)
            if problems:
                raise ValueError('; '.join(problems))
            if not isinstance(default_port, int) or not 1 <= default_port <= 65535:
                raise ValueError('Invalid backend port')
            design = project_lowering_design(blueprint)
            # Tables, not ER compatibility entities, own physical bindings.
            projected_schema = {'entities': [
                {'key': table['name'], 'fields': [
                    {'name': column['name'], 'type': column['type'], 'nullable': column['nullable']}
                    for column in table['columns']]}
                for table in schema['tables']]}

            def checked(value: Any) -> Any:
                if not value.ok:
                    raise ValueError('; '.join(value.errors))
                return value

            symbols = checked(GlobalSymbolPlanner().plan(design, projected_schema, project_manifest))
            files = checked(GlobalFilePlanner(output_root).plan(design, symbols.registry, project_manifest))
            paths = [row['path'] for row in files.registry['files']]
            if len(paths) != len({path.casefold() for path in paths}):
                raise ValueError('File plan contains paths that collide on case-insensitive filesystems')
            types = checked(TypeLowerer().lower(symbols.registry, files.registry))
            attach_module_types(blueprint, design, symbols.registry, files.registry, types)
            bindings_sources, tables = render_bindings(schema, symbols.registry, files.registry,
                                                       storage_type=storage_type)
            backend_database_manifest = {
                **database_manifest, 'orm': 'drizzle', 'tables': tables,
                'generated_files': sorted(set(database_manifest.get('generated_files', [])) | set(bindings_sources)),
            }
            manifests = {'type': types.manifest, 'database': backend_database_manifest}
            sources = {**types.sources, **bindings_sources}
            for kind in ('DB', 'FUNC', 'API'):
                lowered = checked(ModuleSkeletonLowerer().lower(kind, design, symbols.registry, files.registry))
                for row in lowered.manifest['modules']:
                    planned_file = next(file for file in files.registry['files'] if file['path'] == row['path'])
                    row['exports'] = list(dict.fromkeys([*row['exports'], *planned_file['exports']]))
                manifests[kind] = lowered.manifest
                for path, source in lowered.sources.items():
                    if path in sources and sources[path] != source:
                        raise ValueError(f'Conflicting generated source at {path}')
                    sources[path] = source
            glue = checked(BackendGlueLowerer().lower(design, symbols.registry, files.registry, manifests,
                                                     default_port=default_port))
            sources.update(glue.sources)
            planned = {row['path'] for row in files.registry['files']}
            if set(sources) - planned:
                raise ValueError('Generated sources outside frozen file plan')
            database_files = set(database_manifest.get('generated_files', []))
            absent = planned - set(sources) - database_files
            if absent:
                raise ValueError(f'Planned files with no producer: {sorted(absent)}')
            bindings = {row['table']: row['table_symbol'] for row in tables}
            for symbol in symbols.registry['symbols']:
                if symbol['kind'] == 'ENTITY_TABLE' and bindings.get(symbol['entity']) != symbol['symbol']:
                    raise ValueError(f'Database table binding differs from existing lowering: {symbol["entity"]}')
            result.sources = sources
            result.manifest = copy.deepcopy(glue.manifest)
            result.manifest.update(blueprint_hash=content_hash(blueprint), schema_hash=content_hash(schema),
                                   profile=PROFILE, business_implementation='PENDING', generated_files=sorted(sources))
            result.route_registry = glue.route_registry
            result.import_plan = glue.import_plan
            result.tables = {'symbols': symbols.registry, 'files': files.registry, 'types': types.manifest,
                             'database': backend_database_manifest, 'modules_db': manifests['DB'],
                             'modules_func': manifests['FUNC'], 'modules_api': manifests['API'],
                             'routes': glue.route_registry, 'imports': glue.import_plan, 'manifest': result.manifest,
                             'module_plan': {'modules': files.registry['module_locations'],
                                             'types': types.manifest['definitions']}}
            result.warnings = ['Module functions and HTTP handlers contain signatures only; business bodies require implementation.']
        except (AttributeError, KeyError, TypeError, ValueError, RecursionError) as exc:
            result.errors.append(f'BACKEND_LOWERING_INVALID: {exc}')
            result.sources.clear()
        return result


def lower_backend(output_root: Path, store: CompilerArtifactStore, project_manifest: dict[str, Any], *,
                  blueprint: dict[str, Any] | None = None, schema: dict[str, Any] | None = None,
                  database_manifest: dict[str, Any] | None = None, default_port: int = 3000) -> BackendLoweringResult:
    """Read committed artifacts and publish only a completely planned framework."""
    result = BackendLoweringResult()
    try:
        root = output_root.expanduser().resolve()
        if root != store.root.parent:
            raise ValueError('Output root and artifact store differ')
        if not (root / 'backend/package.json').is_file():
            raise ValueError('Initialized backend/package.json is missing')
        blueprint = blueprint if blueprint is not None else json.loads((store.backend_design_root / 'backend_ir.json').read_text())
        schema = schema if schema is not None else json.loads((store.database_root / 'schema.json').read_text())
        database_manifest = database_manifest if database_manifest is not None else json.loads((store.lowering_root / 'database/manifest.json').read_text())
        design_report = store.backend_design_root / 'report.json'
        if design_report.is_file() and json.loads(design_report.read_text()).get('errors'):
            raise ValueError('Backend design report contains unresolved errors')
        result = BackendLowerer().lower(blueprint, schema, project_manifest, database_manifest, root, default_port=default_port)
        if result.ok:
            # Check every destination before writing any source. Repeated lowering
            # cannot silently discard a business body implemented since generation.
            installed_path = store.backend_lowering_root / 'installed-sources.json'
            previous = json.loads(installed_path.read_text()) if installed_path.is_file() else {}
            implementation_files = {row['path'] for row in result.tables['files']['files'] if row['owner'] == 'IMPLEMENTATION'}
            for relative, source in result.sources.items():
                path = (root / relative).resolve()
                if root not in path.parents:
                    raise ValueError(f'Generated path escapes project: {relative}')
                if path.exists():
                    current = path.read_text()
                    if current != source and (relative in previous and current != previous[relative] or relative in implementation_files and relative not in previous):
                        raise ValueError(f'Existing source has implementation changes: {relative}')
            retiring = []
            for relative in previous.keys() - result.sources.keys():
                path = (root / relative).resolve()
                if root not in path.parents:
                    raise ValueError(f'Previous generated path escapes project: {relative}')
                if path.exists():
                    if path.read_text() != previous[relative]:
                        raise ValueError(f'Obsolete generated file has implementation changes: {relative}; preserve or move it before lowering')
                    retiring.append((relative, path))
            result.artifacts.update(store.write_generated_sources(result.sources))
            archive = store.backend_lowering_root / 'retired' / uuid.uuid4().hex
            for relative, path in retiring:
                backup = archive / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                path.rename(backup)
                result.artifacts[f'retired_source:{relative}'] = str(backup)
            write_json_atomic(installed_path, result.sources)
    except (OSError, ValueError, TypeError) as exc:
        result.errors.append(f'BACKEND_LOWERING_FAILED: {exc}')
    report = {'status': 'LOWERED' if result.ok else 'FAILED', 'errors': result.errors, 'warnings': result.warnings,
              'files': sorted(result.sources), 'manifest': result.manifest,
              'route_registry': result.route_registry, 'import_plan': result.import_plan}
    # Failed candidates cannot replace the registries of an installed framework.
    result.artifacts.update(store.write_backend_lowering(report=report, sources=result.sources,
                                                        tables=result.tables if result.ok else {}))
    return result
