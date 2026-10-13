"""Requirement-to-source implementation without design IR, lowering or code bindings."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any

from compiler.build.stage import ProjectBuilder
from compiler.implementation.file_scope import application_path, import_paths
from compiler.implementation.progress import load_tdd_manifest
from compiler.implementation.test_runner import TestRunner, TestSelection
from compiler.preprocessing.traversal import postorder
from model.structured import BaseStructuredAgent
from utils.jsonio import write_json_atomic
from utils.logging import SynchronousLog


def source_path(path: str) -> bool:
    if not path or '\\' in path or ':' in path or '\x00' in path or PurePosixPath(path).as_posix() != path:
        return False
    return application_path(path) or (path.startswith(('backend/src/', 'frontend/src/', 'shared/src/'))
        and '..' not in Path(path).parts and Path(path).suffix in {'.mjs', '.cjs'}) or (path.startswith('backend/sql/') and Path(path).suffix == '.sql'
        and '..' not in Path(path).parts and not Path(path).is_absolute())


SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['files', 'request_files', 'complete'],
    'properties': {
        'complete': {'type': 'boolean'},
        'files': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['file', 'source'], 'properties': {'file': {'type': 'string'}, 'source': {'type': 'string'}}}},
        'request_files': {'type': 'array', 'items': {'type': 'string'}},
    },
    'x-arc-output-mode': 'json_object',
}

INSTRUCTIONS = """Implement the current requirement directly in the existing application source.
There is no schema/backend/frontend design IR, module registry, planned skeleton or lowering stage.
Choose files, types, functions, database tables and UI components from the requirements and actual
source. Implement working business logic, SQL/database initialization, HTTP routes, React UI,
state, handlers and styling. Use the installed project technology; keep prior requirements working.
All layers can be edited together. Wire database initialization, backend server and frontend entrypoints.
Package scripts and supplied test support define runtime entrypoints and database helper contracts;
implement these in application source when needed, rather than changing read-only support files.
Do not generate tests, README, design documents or intermediate architecture JSON.
Tests supplied in read_only_tests are immutable acceptance input; never change tests to pass them.
source_files contains full editable records. file_catalog lists additional discoverable application
files. Request unread files through request_files before modifying them; return files:[], complete:false
for requests. Only loaded files may be replaced. New files may be created within creation_roots using
safe text source extensions. Known configuration files can be replaced only after loading them.
Return complete final file contents, not snippets. Preserve co-located behavior and public contracts.
Use complete literal Tailwind utilities or existing CSS. Match supplied visual analyses.
shared_image_files identifies source previously touched by requirements with the same image; these
requirements describe the same screen. Inspect and reuse it before adding duplicate screens.
Compiler/test feedback may require changes across any application layer; request missing files.
A valid unchanged implementation may return files:[], request_files:[], complete:true.
A large implementation may use several compiling batches with complete:false. Do not leave
unresolved imports. complete:true means the entire requirement is implemented.
"""


class SourceFileScope:
    """Source-only retrieval; never opens .arc/design, lowering or binding artifacts."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def catalog(self) -> list[str]:
        paths = []
        for directory in ('backend/src', 'backend/sql', 'frontend/src', 'shared/src'):
            folder = self.root / directory
            if folder.is_dir():
                paths.extend(p.relative_to(self.root).as_posix() for p in folder.rglob('*')
                             if p.is_file() and self.root in p.resolve().parents)
        from compiler.implementation.file_scope import CONFIG_FILES
        paths.extend(path for path in CONFIG_FILES if (self.root / path).is_file())
        return sorted({path for path in paths if source_path(path)})

    def read(self, path: str) -> str:
        target = (self.root / path).resolve()
        if self.root not in target.parents or not target.is_file():
            raise ValueError(f'Unsafe or missing source: {path}')
        return target.read_text(encoding='utf-8')

    def select(self, requirement: dict[str, Any], requested: set[str], preferred: set[str]) -> dict[str, str]:
        import re
        catalog = self.catalog()
        if requested - set(catalog):
            raise ValueError('Requested source is outside the application catalog')
        terms = set(re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]', json.dumps(requirement, ensure_ascii=False).lower()))
        scored = []
        for path in catalog:
            text = self.read(path)
            words = set(re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]', (path + '\n' + text[:12000]).lower()))
            scored.append((len(words & terms), path))
        entrypoints = {'backend/src/server.ts', 'backend/src/app.ts', 'frontend/src/App.tsx',
                      'frontend/src/main.tsx', 'frontend/src/index.css', 'package.json',
                      'backend/package.json', 'frontend/package.json', 'shared/src/index.ts'}
        mandatory = requested | (entrypoints & set(catalog))
        seeds = mandatory | (preferred & set(catalog)) | {path for _, path in sorted(scored, reverse=True)[:16]}
        loaded: dict[str, str] = {}
        pending = sorted(seeds, key=lambda path: (path not in mandatory, path not in preferred, path))
        size = 0
        while pending:
            path = pending.pop(0)
            if path in loaded:
                continue
            source = self.read(path)
            if size + len(source) > 300_000:
                if path in mandatory:
                    raise ValueError('Requested source context exceeds budget; request fewer files')
                continue
            loaded[path] = source
            size += len(source)
            pending.extend(sorted(import_paths(self.root, path, source) - loaded.keys()))
        return loaded


class DirectImplementation:
    def __init__(self, model: Any, root: Path, requirements: dict[str, Any], dependencies: dict[str, Any],
                 *, visuals: list[dict[str, Any]] | None = None, iterations: int = 5) -> None:
        self.root = root.resolve()
        self.requirements = requirements
        self.dependencies = dependencies
        self.visuals = [{**row, 'id': row.get('id', row.get('reference_id'))} for row in visuals or []]
        self.iterations = iterations
        self.scope = SourceFileScope(root)
        self.log = SynchronousLog('DirectImplementation', workspace_root=self.root)
        self.runner = TestRunner(root)
        self.manifest = load_tdd_manifest(root)
        if self.manifest and self.manifest.get('status') != 'TESTS_FROZEN':
            raise ValueError('Existing acceptance tests must be frozen before direct implementation')
        self.progress_path = root / '.arc/implementation/direct_progress.json'
        self.progress = {'mode': 'direct-source', 'nodes': {}}
        self.fingerprint = hashlib.sha256(json.dumps(requirements, sort_keys=True).encode()).hexdigest()
        self.agent = BaseStructuredAgent(model, schema_name='direct_source_implementation',
            instructions=INSTRUCTIONS, output_schema=SCHEMA, retries=2,
            model_log=self._model_log, trace=self.log.info, agent_name='DirectImplementation')

    def _order(self) -> list[str]:
        # Respect explicit cross-requirement dependencies as well as child-before-parent ordering.
        original = postorder(self.requirements)
        remaining = set(original)
        order = []
        graph = self.dependencies.get('requirement_dependencies', {})
        while remaining:
            ready = [rid for rid in original if rid in remaining and not (set(graph.get(rid, [])) & remaining)]
            if not ready:
                raise ValueError('Requirement dependency cycle')
            order.extend(ready)
            remaining.difference_update(ready)
        return order

    def _model_log(self, payload: dict[str, Any]) -> None:
        import time
        write_json_atomic(self.root / '.arc/model_logs/direct' / f'{time.time_ns()}.json', payload)

    def _build_errors(self) -> list[str]:
        result = ProjectBuilder(self.root).build()
        return [] if result.ok else list(result.errors) or ['Project build failed']

    def _test_errors(self, ids: list[str]) -> list[str]:
        if not self.manifest:
            return []
        errors = []
        for rid in ids:
            if not any(row.get('requirement_id') == rid for row in self.manifest.get('files', [])):
                continue
            result = self.runner.run(TestSelection(rid), test_manifest=self.manifest)
            if not result.ok:
                errors.append(json.dumps(asdict(result), ensure_ascii=False))
        return errors

    def _apply(self, files: list[dict[str, str]], sources: dict[str, str]) -> tuple[list[str], list[str]]:
        snapshots: dict[Path, bytes | None] = {}
        changed = []
        try:
            for row in files:
                path, source = row['file'], row['source']
                target = self.root / path
                if not source_path(path) or self.root not in target.resolve().parents:
                    raise ValueError(f'Unsafe application path: {path}')
                if target.is_symlink():
                    raise ValueError(f'Cannot replace symlink: {path}')
                if target.exists():
                    if path not in sources or target.read_text(encoding='utf-8') != sources[path]:
                        raise ValueError(f'File not loaded or changed after context creation: {path}')
                    if sources[path] == source:
                        continue
                    snapshots[target] = target.read_bytes()
                else:
                    if not path.startswith(('backend/src/', 'backend/sql/', 'frontend/src/', 'shared/src/')):
                        raise ValueError(f'Cannot create configuration file: {path}')
                    snapshots[target] = None
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                                     prefix='.arc-write-', delete=False) as stream:
                        temporary = Path(stream.name)
                        stream.write(source)
                    os.replace(temporary, target)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                changed.append(path)
            errors = self._build_errors()
            if not errors:
                return changed, []
        except Exception as exc:
            errors = [str(exc)]
        for target, previous in reversed(list(snapshots.items())):
            try:
                if previous is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(previous)
            except OSError as exc:
                errors.append(f'Rollback failed for {target}: {exc}')
        return [], errors

    def run(self, *, resume: bool = False) -> dict[str, Any]:
        if resume and self.progress_path.is_file():
            self.progress = json.loads(self.progress_path.read_text())
            if self.progress.get('requirement_sha256') != self.fingerprint:
                raise ValueError('Resume requirements differ from the direct implementation run')
        self.progress['requirement_sha256'] = self.fingerprint
        nodes = self.requirements['nodes']
        completed = [rid for rid, row in self.progress['nodes'].items() if row['status'] == 'IMPLEMENTED']
        order = self._order()
        for rid in order:
            if resume and rid in completed:
                continue
            self.log.info(f'DIRECT_IMPLEMENTATION_STARTED requirement={rid}')
            requested: set[str] = set()
            feedback: list[str] = list(self.progress['nodes'].get(rid, {}).get('errors', []))
            changed: set[str] = set(self.progress['nodes'].get(rid, {}).get('changed_files', []))
            self.progress['nodes'][rid] = {'status': 'STARTED', 'changed_files': sorted(changed), 'errors': []}
            write_json_atomic(self.progress_path, self.progress)
            related = list(dict.fromkeys([*nodes[rid].get('children_ids', []),
                *self.dependencies.get('requirement_dependencies', {}).get(rid, [])]))
            visual = [row for row in self.visuals if rid in row.get('requirement_ids', [])]
            image_owners = {owner for row in visual for owner in row.get('requirement_ids', []) if owner != rid}
            image_files = {path for owner in image_owners for path in self.progress['nodes'].get(owner, {}).get('changed_files', [])}
            success = False
            for iteration in range(self.iterations):
                for expansion in range(5):
                    evidence = '\n'.join(feedback)
                    evidence_paths = {path for path in self.scope.catalog() if path in evidence or path.partition('/')[2] in evidence}
                    sources = self.scope.select(nodes[rid], requested, image_files | changed | evidence_paths)
                    test_files = {row['test_file']: self.scope.read(row['test_file']) for row in (self.manifest or {}).get('files', [])
                                  if row.get('requirement_id') in {*completed, rid}}
                    for helper in (self.root / 'tests/support').glob('*.ts'):
                        test_files[helper.relative_to(self.root).as_posix()] = self.scope.read(helper.relative_to(self.root).as_posix())
                    context = {'requirement_id': rid, 'requirement': nodes[rid], 'iteration': iteration,
                        'related_requirements': [nodes[key] for key in related if key in nodes],
                        'completed_requirements': [nodes[key] for key in completed if key in nodes],
                        'source_files': sources, 'file_catalog': self.scope.catalog(),
                        'creation_roots': ['backend/src/', 'backend/sql/', 'frontend/src/', 'shared/src/'],
                        'visual_references': visual, 'shared_image_files': sorted(image_files),
                        'read_only_tests': test_files, 'feedback': feedback}
                    if len(json.dumps(context, ensure_ascii=False)) > 600_000:
                        raise ValueError(f'Direct implementation context exceeds budget for {rid}')
                    def validate(output):
                        if not isinstance(output, dict) or set(output) != {'files', 'request_files', 'complete'}:
                            return ['Return files, request_files, complete']
                        if not isinstance(output['complete'], bool) or not isinstance(output['files'], list) or not isinstance(output['request_files'], list):
                            return ['Invalid output types']
                        if output['request_files']:
                            if expansion == 4 or output['files'] or output['complete'] or any(not isinstance(path, str) or path not in context['file_catalog'] or path in sources for path in output['request_files']):
                                return ['Request only unloaded catalog files, no edits, at most four expansions']
                        paths = set()
                        for row in output['files']:
                            if not isinstance(row, dict) or set(row) != {'file', 'source'} or not all(isinstance(value, str) for value in row.values()):
                                return ['Each file needs a string file and source']
                            path = row['file']
                            if path in paths or not source_path(path) or (path in context['file_catalog'] and path not in sources):
                                return ['Edit only loaded files or safe new source paths']
                            paths.add(path)
                        if not output['files'] and not output['request_files'] and not output['complete']:
                            return ['Empty batch must be complete or request more files']
                        return []
                    result = self.agent.invoke(context, validate=validate)
                    if not result.ok or result.output is None:
                        feedback = list(result.errors)
                        break
                    output = result.output
                    if output['request_files']:
                        requested.update(output['request_files'])
                        self.log.info(f'DIRECT_SOURCE_REQUEST requirement={rid} files={output["request_files"]}')
                        continue
                    paths, feedback = self._apply(output['files'], sources)
                    changed.update(paths)
                    self.progress['nodes'][rid].update(changed_files=sorted(changed), errors=feedback)
                    write_json_atomic(self.progress_path, self.progress)
                    if not feedback and output['complete']:
                        feedback = self._test_errors([*completed, rid])
                        success = not feedback
                    break
                if success:
                    break
            if not success and not feedback:
                feedback = ['Requirement did not complete within the implementation budget']
            self.progress['nodes'][rid] = {'status': 'IMPLEMENTED' if success else 'FAILED',
                'changed_files': sorted(changed), 'errors': feedback}
            write_json_atomic(self.progress_path, self.progress)
            self.log.info(f'DIRECT_IMPLEMENTATION_FINISHED requirement={rid} status={self.progress["nodes"][rid]["status"]}')
            if not success:
                # Stop at the failed requirement rather than implementing dependents
                # on top of known-broken prerequisite behavior.
                break
            completed.append(rid)
        errors = self._build_errors() + self._test_errors(completed)
        errors.extend(error for row in self.progress['nodes'].values() if row['status'] == 'FAILED' for error in row.get('errors', []))
        failed = [rid for rid in order if self.progress['nodes'].get(rid, {}).get('status') != 'IMPLEMENTED']
        return {'ok': not failed and not errors, 'states': {rid: self.progress['nodes'].get(rid, {}).get('status', 'NOT_STARTED') for rid in order},
                'failed_nodes': failed, 'errors': errors, 'progress': str(self.progress_path),
                'verification': 'frozen-tests-and-build' if self.manifest else 'build-only'}
