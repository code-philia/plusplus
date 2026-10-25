"""Normalize requirement identities, reusable images and document-local typed references."""
from __future__ import annotations

import copy
import re
from pathlib import PurePosixPath


REFERENCE = re.compile(r'\\ref\[(image|requirement|scenario|step)\]\(([^()\r\n]+)\)')


class RequirementDocument:
    def __init__(self, source):
        self.source, self.errors = source, []
        self.nodes, self.order, self.images = {}, [], {}
        self.scenarios, self.steps = {}, {}
        self.active = set()

    def error(self, message, pointer):
        self.errors.append(f'ARC1100: {message} ({self.source}:{pointer})')

    def mapping(self, value, fields, pointer):
        if not isinstance(value, dict):
            self.error('Expected a mapping', pointer)
            return {}
        unknown = set(value) - set(fields)
        if unknown:
            self.error('Unknown fields: ' + ', '.join(sorted(map(str, unknown))), pointer)
        return value

    def collection(self, value, pointer):
        if not isinstance(value, list):
            self.error('Expected a list', pointer)
            return []
        return value

    def identity(self, raw, registry, pointer):
        value = raw.get('id')
        if not isinstance(value, str) or not value.strip():
            self.error('A nonempty string id is required', pointer)
            return '<invalid:' + pointer + '>'
        value = value.strip()
        if value in registry:
            self.error('Duplicate id: ' + value, pointer)
        return value

    def name(self, raw, pointer):
        value = raw.get('name')
        if not isinstance(value, str) or not value.strip():
            self.error('A nonempty string name is required', pointer)
            return ''
        return value.strip()

    def normalize(self, payload):
        root = self.requirement(payload, None, '/')
        registries = {'requirement': self.nodes, 'scenario': self.scenarios,
                      'step': self.steps, 'image': self.images}
        for node in self.nodes.values():
            node['description']['image'] = [copy.deepcopy(self.images[row['id']])
                                            for row in node['description']['image'] if row['id'] in self.images]
        for node in self.nodes.values():
            referenced, contexts = {}, []
            for scenario in node['scenarios']:
                for step in scenario['steps']:
                    for reference in step['references']:
                        kind, target = reference['type'], reference['target_id']
                        registry = registries[kind]
                        if target not in registry:
                            self.error(f'Unresolved {kind} reference: {target}', step['id'])
                            continue
                        referenced[kind, target] = registry[target]
            image_ids = [row['id'] for row in node['description']['image']]
            local_images = set(image_ids)
            image_ids += [target for kind, target in referenced if kind == 'image']
            for (kind, target), value in referenced.items():
                if (kind == 'requirement' and target == node['id'] or kind == 'image' and target in local_images
                        or kind in {'scenario', 'step'} and value['requirement_id'] == node['id']):
                    continue
                if kind == 'requirement':
                    value = {key: value[key] for key in ('id', 'name', 'description', 'scenarios')}
                contexts.append({'type': kind, 'target_id': target, 'value': copy.deepcopy(value)})
            if contexts:
                node['reference_context'] = contexts
            images = [copy.deepcopy(self.images[key]) for key in dict.fromkeys(image_ids) if key in self.images]
            node['pages'] = [{'name': image.get('page'), 'reference': image['reference'], 'image_id': image['id']}
                             for image in images]
            node['visual_references'] = sorted({image['reference'] for image in images})
        return {'root_id': root, 'node_order': self.order, 'nodes': dict(sorted(self.nodes.items())),
                'atomic_units': sorted(rid for rid, node in self.nodes.items() if node['type'] == 'ATOMIC'),
                'folder_nodes': sorted(rid for rid, node in self.nodes.items() if node['type'] == 'FOLDER'),
                'images': copy.deepcopy(self.images)}

    def requirement(self, value, parent, pointer):
        if id(value) in self.active:
            self.error('Recursive YAML aliases are not a requirement tree', pointer)
            return ''
        raw = self.mapping(value, ('id', 'name', 'description', 'scenarios', 'children'), pointer)
        self.active.add(id(value))
        rid = self.identity(raw, self.nodes, pointer)
        children = self.collection(raw.get('children', []), pointer + '/children')
        node = {'id': rid, 'name': self.name(raw, pointer),
                'description': self.description(raw.get('description', {}), pointer + '/description'),
                'type': 'FOLDER' if children else 'ATOMIC', 'parent_id': parent,
                'children_ids': [], 'scenarios': [],
                'source': {'document': self.source, 'pointer': pointer}}
        if rid not in self.nodes:
            self.nodes[rid] = node
            self.order.append(rid)
        for index, scenario in enumerate(self.collection(raw.get('scenarios', []), pointer + '/scenarios')):
            node['scenarios'].append(self.scenario(scenario, rid, f'{pointer}/scenarios/{index}'))
        if node['scenarios']:
            node['type'] = 'ATOMIC'
        for index, child in enumerate(children):
            child_id = self.requirement(child, rid, f'{pointer.rstrip("/")}/children/{index}')
            if child_id:
                node['children_ids'].append(child_id)
        self.active.remove(id(value))
        return rid

    def description(self, value, pointer):
        raw = self.mapping(value, ('text', 'image'), pointer)
        text = raw.get('text', '')
        if not isinstance(text, str):
            self.error('description.text must be a string', pointer)
            text = ''
        images = []
        for index, value in enumerate(self.collection(raw.get('image', []), pointer + '/image')):
            location = f'{pointer}/image/{index}'
            image = self.mapping(value, ('id', 'reference', 'page'), location)
            identity = image.get('id')
            if not isinstance(identity, str) or not identity.strip():
                self.error('Image id must be a nonempty string', location)
                continue
            identity = identity.strip()
            existing = self.images.get(identity)
            reference = image.get('reference')
            if 'reference' in image:
                if (not isinstance(reference, str) or not reference.strip() or '\\' in reference
                        or PurePosixPath(reference).is_absolute() or ':' in reference
                        or '..' in PurePosixPath(reference).parts):
                    self.error('Image reference must be a nonempty document-relative path using /', location)
                    continue
                if existing and reference != existing['reference']:
                    self.error('Image id cannot change its reference: ' + identity, location)
                    continue
            elif not existing:
                self.error('First image occurrence requires reference: ' + identity, location)
                continue
            record = existing if existing is not None else {'id': identity, 'reference': reference}
            if 'page' in image:
                page = image['page']
                if not isinstance(page, str) or not page.strip():
                    self.error('Image page must be a nonempty string when supplied', location)
                elif 'page' in record and record['page'] != page:
                    self.error('Image id cannot change its page: ' + identity, location)
                else:
                    record['page'] = page
            self.images[identity] = record
            images.append({'id': identity})
        return {'text': text, 'image': images}

    def scenario(self, value, rid, pointer):
        raw = self.mapping(value, ('id', 'name', 'steps'), pointer)
        identity = self.identity(raw, self.scenarios, pointer)
        scenario = {'id': identity, 'name': self.name(raw, pointer), 'steps': []}
        if identity not in self.scenarios:
            self.scenarios[identity] = {'requirement_id': rid, **scenario}
        for index, value in enumerate(self.collection(raw.get('steps', []), pointer + '/steps')):
            scenario['steps'].append(self.step(value, rid, identity, f'{pointer}/steps/{index}'))
        return scenario

    def step(self, value, rid, scenario_id, pointer):
        raw = self.mapping(value, ('id', 'Given', 'When', 'Then', 'references'), pointer)
        identity = self.identity(raw, self.steps, pointer)
        step = {'id': identity}
        for key in ('Given', 'When', 'Then'):
            text = raw.get(key)
            if text is not None and not isinstance(text, str):
                self.error(key + ' must be text or null', pointer)
                text = ''
            step[key] = text if text is not None else ''
        references = []
        for reference in self.collection(raw.get('references', []), pointer + '/references'):
            matched = REFERENCE.fullmatch(reference) if isinstance(reference, str) else None
            if not matched:
                self.error('Expected a typed reference: \\ref[type](ID)', pointer)
                continue
            item = {'type': matched[1], 'target_id': matched[2]}
            if item not in references:
                references.append(item)
        step['references'] = references
        if identity not in self.steps:
            self.steps[identity] = {'requirement_id': rid, 'scenario_id': scenario_id, **step}
        return step
