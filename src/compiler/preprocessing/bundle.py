"""Resolve local bundle references and retain evidence for compiler stages."""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .er import normalize_er, parse_mermaid

REFERENCE = re.compile(r'\[(ER|OPA|BPMN):\s*([^\s\]#]+)(?:#([^\s\]]+))?\]')


class BundleLoader:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.files: dict[str, str] = {}
        self.diagnostics: list[dict[str, Any]] = []

    def diagnostic(self, code: str, message: str, *, severity='warning', **context):
        self.diagnostics.append({'severity': severity, 'code': code, 'message': message, **context})

    def path(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative or ':' in relative:
            raise ValueError(f'Invalid local bundle path: {relative!r}')
        relative = relative.replace('\\', '/')
        path = (self.root / relative).resolve()
        if Path(relative).is_absolute() or not path.is_relative_to(self.root):
            raise ValueError(f'Bundle reference escapes its root: {relative}')
        if not path.is_file():
            raise ValueError(f'Missing bundle reference: {relative}')
        return path

    def record(self, relative: str, expected: str | None = None) -> Path:
        path = self.path(relative)
        name = path.relative_to(self.root).as_posix()
        if name not in self.files:
            with path.open('rb') as stream:
                self.files[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
        if expected and self.files[name] != expected:
            raise ValueError(f'Bundle reference hash mismatch: {relative}')
        return path

    def read(self, relative: str) -> str:
        return self.record(relative).read_text(encoding='utf-8-sig')

    def json(self, relative: str) -> dict[str, Any]:
        value = json.loads(self.read(relative))
        if not isinstance(value, dict):
            raise ValueError(f'{relative}: expected a JSON object')
        return value

    def load(self, requirements: dict[str, Any]) -> dict[str, Any]:
        bindings = {}
        for rid, node in requirements['nodes'].items():
            refs = []
            for match in REFERENCE.finditer(node.get('description', '')):
                kind, path, fragment = match.groups()
                path = path.replace('\\', '/')
                self.record(path)
                ref = {'kind': kind, 'path': path, 'fragment': fragment, 'resolved': False}
                if ref not in refs:
                    refs.append(ref)
            bindings[rid] = {'references': refs, 'entities': [], 'crud': [], 'permissions': [],
                             'processes': [], 'ui_states': []}
        er = self.er(bindings)
        authorization = self.authorization()
        processes = self.processes(bindings)
        ui = self.ui(requirements, bindings)
        entities = er.get('entity_aliases', {})
        relationships = {row['source_id']: row['key'] for row in er.get('relationships', []) if row['source_id']}
        permissions = {row['id'] for row in authorization.get('model', {}).get('permissions', [])}
        for rid, binding in bindings.items():
            for ref in binding['references']:
                fragment, kind = ref['fragment'], ref['kind']
                if kind == 'ER':
                    target = entities.get(fragment) or relationships.get(fragment)
                    if target:
                        ref.update(resolved=True, target=target)
                        if fragment in entities and target not in binding['entities']:
                            binding['entities'].append(target)
                    else:
                        self.diagnostic('BUNDLE_ER_REFERENCE_UNRESOLVED',
                            f'ER fragment {fragment!r} is not published; no guessed binding was created.',
                            node_id=rid, path=ref['path'])
                elif kind == 'OPA' and fragment in permissions and ref['path'] == 'reference/authorization/data.json':
                    ref.update(resolved=True, target=fragment)
                    binding['permissions'].append(fragment)
                elif kind == 'BPMN' and f'{ref["path"]}#{fragment}' in processes:
                    ref.update(resolved=True, target=fragment)
                    binding['processes'].append(fragment)
                else:
                    self.diagnostic('BUNDLE_REFERENCE_UNRESOLVED', f'Cannot resolve {kind} fragment {fragment!r}.',
                                    node_id=rid, path=ref['path'])
            # CRUD only comes from the operation list beside the entity citation,
            # never from merely mentioning an entity or its prerequisite node.
            for line in requirements['nodes'][rid].get('description', '').splitlines():
                match = re.search(r'\[ER:\s*[^\]#]+#([^\]]+)\]\s*\(([^)]+)\)', line)
                if match and match[1] in entities:
                    operations = [x.strip().lower() for x in match[2].split(',')]
                    if set(operations) <= {'create', 'read', 'update', 'delete'}:
                        binding['crud'].append({'entity': entities[match[1]], 'operations': operations})
        for entity in er.get('entities', []):
            entity['requirement_ids'] = [rid for rid, binding in bindings.items()
                                         if entity['key'] in binding['entities']]
        for relation in er.get('relationships', []):
            explicit = [rid for rid, binding in bindings.items() if any(
                ref.get('target') == relation['key'] and ref['kind'] == 'ER' for ref in binding['references'])]
            endpoints = [rid for rid, binding in bindings.items()
                         if {relation['parent'], relation['child']} & set(binding['entities'])]
            relation['requirement_ids'] = explicit or endpoints
            relation['binding_basis'] = 'explicit_reference' if explicit else 'endpoint_references'
        identity_path = 'reference/identity/model.json'
        identity_proposal = self.json(identity_path) if (self.root / identity_path).is_file() else None
        return {'schema_version': 1, 'files': dict(sorted(self.files.items())), 'bindings': bindings,
                'er': er, 'ui': ui, 'authorization': authorization, 'processes': processes,
                'identity_proposal': identity_proposal,
                'diagnostics': self.diagnostics}

    def er(self, bindings):
        sources = {ref['path'] for binding in bindings.values() for ref in binding['references'] if ref['kind'] == 'ER'}
        conventional = 'reference/er/model.json'
        if (self.root / conventional).is_file():
            # The lossless companion is authoritative when published in THIS bundle.
            sources = (sources - {'reference/er/model.mmd'}) | {conventional}
        elif not sources and (self.root / 'reference/er/model.mmd').is_file():
            sources = {'reference/er/model.mmd'}
        result = {'entities': [], 'relationships': [], 'entity_aliases': {}}
        for path in sorted(sources):
            parsed = normalize_er(self.json(path), path) if path.endswith('.json') else parse_mermaid(self.read(path), path)
            existing = {row['key'] for row in result['entities']}
            if existing & {row['key'] for row in parsed['entities']}:
                raise ValueError(f'{path}: duplicate ER symbols across documents')
            result['entities'].extend(parsed['entities'])
            result['relationships'].extend(parsed['relationships'])
            result['entity_aliases'].update(parsed['entity_aliases'])
            self.diagnostics.extend(parsed['diagnostics'])
        return result

    def authorization(self):
        path = 'reference/authorization/data.json'
        if not (self.root / path).is_file():
            return {}
        model = self.json(path)['authorization']
        for key in ('roles', 'resources', 'actions', 'permissions', 'role_bindings'):
            if not isinstance(model.get(key), list):
                raise ValueError(f'{path}: {key} must be an array')
        result = {'model': model, 'source': path}
        for name in ('authz.rego', 'input.schema.json', 'authz_test.rego'):
            relative = 'reference/authorization/' + name
            if (self.root / relative).is_file():
                result[name] = self.json(relative) if name.endswith('.json') else self.read(relative)
        if model.get('completeness') != 'complete':
            self.diagnostic('BUNDLE_AUTHORIZATION_PARTIAL', 'Authorization data does not claim complete coverage.', path=path)
        return result

    def processes(self, bindings):
        paths = {ref['path'] for binding in bindings.values() for ref in binding['references'] if ref['kind'] == 'BPMN'}
        result = {}
        for path in sorted(paths):
            tree = ET.fromstring(self.read(path))
            for process in tree.findall('.//{*}process'):
                result[f'{path}#{process.attrib["id"]}'] = {
                    'source': path, 'id': process.attrib['id'], 'name': process.get('name', ''),
                    'nodes': [{'id': item.get('id'), 'kind': item.tag.rsplit('}', 1)[-1],
                               'name': item.get('name', '')} for item in process
                              if item.get('id') and item.tag.rsplit('}', 1)[-1] not in {'sequenceFlow', 'laneSet'}],
                    'flows': [{'id': flow.get('id'), 'source': flow.get('sourceRef'), 'target': flow.get('targetRef'),
                               'condition': ''.join(flow.itertext()).strip()}
                              for flow in process.findall('{*}sequenceFlow')],
                    'lanes': [{'id': lane.get('id'), 'name': lane.get('name', ''),
                               'nodes': [item.text for item in lane.findall('{*}flowNodeRef')]}
                              for lane in process.findall('.//{*}lane')],
                }
        return result

    def ui(self, requirements, bindings):
        base = 'reference/ui/'
        if not (self.root / (base + 'index.json')).is_file():
            return {}
        index, sitemap = self.json(base + 'index.json'), self.json(base + 'pages.json')
        if index.get('schema_version') != 2:
            raise ValueError('Unsupported UI bundle schema_version; expected 2')
        captures, pages, states, declared_states = {}, {}, {}, set()
        for capture in index['captures']:
            if capture['id'] in captures:
                raise ValueError(f'Duplicate UI capture: {capture["id"]}')
            for record in capture['files'].values():
                self.record(record['path'], record.get('sha256'))
            captures[capture['id']] = capture
        for page in sitemap['pages']:
            if page['id'] in pages:
                raise ValueError(f'Duplicate UI page: {page["id"]}')
            pages[page['id']] = {key: page.get(key) for key in ('id', 'title', 'menu_path', 'route', 'status', 'states')}
            for entry in page['states']:
                if entry['id'] in declared_states:
                    raise ValueError(f'Duplicate UI state: {entry["id"]}')
                declared_states.add(entry['id'])
                if not entry.get('collected'):
                    self.diagnostic('BUNDLE_UI_UNCOLLECTED_STATE',
                        'Declared state has no published evidence; it remains a coverage gap.',
                        page_id=page['id'], state_id=entry['id'])
                    continue
                state = self.json(base + f'states/{entry["id"]}.json')
                if state.get('state') != entry['id'] or state.get('page') != page['id']:
                    raise ValueError(f'UI state identity mismatch: {entry["id"]}')
                states[entry['id']] = {key: state.get(key) for key in
                    ('state', 'page', 'kind', 'item', 'parent', 'status', 'settled', 'contract', 'depends_on', 'regions', 'tokens')}
                states[entry['id']]['recipe'] = self.json(base + f'recipes/{entry["id"]}.json')
                if states[entry['id']]['recipe'].get('state') != entry['id']:
                    raise ValueError(f'UI recipe identity mismatch: {entry["id"]}')
        accepted = []
        for link in index['node_links']:
            rid, sid, cid = link['node_id'], link['state_id'], link['capture_id']
            if rid not in bindings or sid not in states or cid not in captures:
                raise ValueError(f'UI link refers to unknown requirement/state/capture: {link}')
            if captures[cid].get('state_id') != sid or captures[cid].get('status') != 'complete':
                raise ValueError(f'UI link capture is incomplete or belongs to another state: {cid}')
            kind = link['link_type']
            leaf = not requirements['nodes'][rid]['children_ids']
            if kind not in {'primary', 'partial', 'shared'} or (kind == 'primary' and not leaf) or (kind == 'shared' and leaf):
                raise ValueError(f'Invalid UI link type/ownership: {link}')
            accepted.append(link)
            bindings[rid]['ui_states'].append({'state_id': sid, 'page_id': states[sid]['page'],
                                               'capture_id': cid, 'link_type': kind})
        for sid, state in states.items():
            if state.get('parent') and state['parent'] not in states:
                raise ValueError(f'UI state {sid} has an unknown parent')
            if state.get('parent') and states[state['parent']]['page'] != state['page']:
                raise ValueError(f'UI state {sid} has a parent from another page')
        for rid, binding in bindings.items():
            if not requirements['nodes'][rid]['children_ids'] and not binding['ui_states']:
                self.diagnostic('BUNDLE_UI_UNCOVERED_REQUIREMENT', 'No accepted UI evidence; requirement remains in scope.', node_id=rid)
        for pid in sitemap.get('unlinked', []):
            self.diagnostic('BUNDLE_UI_UNLINKED_PAGE', 'Page has no accepted requirement association.', page_id=pid)
        result = {'origin': sitemap.get('origin'), 'menu': sitemap['menu'], 'pages': pages,
                  'states': states, 'captures': captures, 'links': accepted, 'unlinked': sitemap.get('unlinked', [])}
        for name, key in (('design-system.json', 'design_system'), ('validation.json', 'validation')):
            if (self.root / (base + name)).is_file():
                result[key] = self.json(base + name)
        return result
