"""Structural and deterministic contract checks for design retries and lowering."""
from __future__ import annotations

from typing import Any
import re
import json

from .contracts import IR_SCHEMA


def route_key(module: dict[str, Any]) -> tuple[str, str]:
    """Parameter names do not distinguish Express route identities."""
    return module['method'].upper(), re.sub(r':[A-Za-z_][A-Za-z0-9_]*', ':parameter', module['path'].rstrip('/') or '/')


class ModuleConflictIndex:
    def __init__(self, modules: list[dict[str, Any]]) -> None:
        self.by_id = {row['id']: row for row in modules}
        self.names: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.routes: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in modules:
            self.names.setdefault(self.name_key(row), []).append(row)
            if row['kind'] == 'API':
                self.routes.setdefault(route_key(row), []).append(row)

    @staticmethod
    def name_key(row: dict[str, Any]) -> tuple[str, str]:
        return row['kind'], re.sub(r'[^a-z0-9]', '', row['id'].split('::')[-1].split('.')[-1].lower())

    def conflicts(self, value: dict[str, Any]) -> list[dict[str, Any]]:
        matches = []
        for row in value['new_modules']:
            found: dict[str, set[str]] = {}
            for existing in self.names.get(self.name_key(row), []):
                found.setdefault(existing['id'], set()).add('name')
            if row['id'] in self.by_id:
                found.setdefault(row['id'], set()).add('id')
            if row['kind'] == 'API':
                for existing in self.routes.get(route_key(row), []):
                    found.setdefault(existing['id'], set()).add('route')
            for key, reasons in found.items():
                matches.append({'module_id': row['id'], 'existing_module_id': key,
                                'reasons': sorted(reasons), 'existing': self.by_id[key]})
        return matches

    def errors(self, value: dict[str, Any], conflicts: list[dict[str, Any]]) -> list[str]:
        errors = []
        for row in conflicts:
            errors.append(f"Module {row['module_id']} conflicts by {row['reasons']} with {row['existing_module_id']}. "
                          "Read conflict_candidates; reuse the existing module only if it can remain completely unchanged, "
                          "or design a different responsibility with an unoccupied name and route. "
                          "Duplicate names, IDs and routes are not allowed.")
        pending = list(value['new_modules'])
        seen = set()
        names = {}
        routes = {}
        for row in pending:
            if row['id'] in seen:
                errors.append(f"Duplicate new module ID: {row['id']}")
            seen.add(row['id'])
            name = self.name_key(row)
            if name in names:
                errors.append(f"New modules share a name: {names[name]} and {row['id']}; reuse one or rename the distinct module.")
            names[name] = row['id']
            if row['kind'] == 'API':
                key = route_key(row)
                if key in routes:
                    errors.append(f"New API route {key} is shared by {routes[key]} and {row['id']}; return one entrypoint.")
                routes[key] = row['id']
        return errors


def context_request_errors(value: dict[str, Any], *, known: dict[str, set[str]],
                           loaded: dict[str, set[str]], expansions_remaining: int) -> list[str]:
    """Validate discovery requests inside the same retry loop as JSON shapes."""
    requests = {field: set(value.get(field, [])) for field in known}
    if not any(requests.values()):
        return []
    errors = []
    design_fields = [field for field in ('new_modules', 'updated_modules', 'semantic_type_changes', 'new_types', 'resolved_calls', 'api_ids') if value.get(field)]
    if design_fields:
        errors.append(f"Context request cannot include nonempty {design_fields}. Choose a context-only reply "
                      "with new_modules:[] and api_ids:[] (API), or a design-only reply without requested_* fields.")
    for field, keys in requests.items():
        unknown = sorted(keys - known[field])
        already_loaded = sorted(keys & loaded[field])
        if unknown:
            errors.append(f"{field}: unknown or unavailable in this layer: {unknown}. Request exact catalog/index IDs only. "
                          "New modules/types and forward calls must be designed in their proper layer, not requested. "
                          "Tables must already exist in the schema.")
        if already_loaded:
            errors.append(f"{field}: already loaded: {already_loaded}. Use these contracts directly and remove the request.")
    if not expansions_remaining:
        errors.append("Context expansion limit reached. Return a design using the loaded contracts without requested_* fields.")
    return errors


def shape_errors(value: Any, schema: dict[str, Any], path: str = '$', definitions: dict[str, Any] | None = None) -> list[str]:
    definitions = {**(definitions or {}), **schema.get('$defs', {})}
    if '$ref' in schema:
        return shape_errors(value, definitions[schema['$ref'].rsplit('/', 1)[1]], path, definitions)
    if 'anyOf' in schema:
        alternatives = [shape_errors(value, branch, path, definitions) for branch in schema['anyOf']]
        return [] if any(not errors for errors in alternatives) else min(alternatives, key=len)
    if 'enum' in schema and value not in schema['enum']:
        return [f'{path}: expected one of {schema["enum"]}']
    kind = schema.get('type')
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'boolean': isinstance(value, bool),
             'integer': isinstance(value, int) and not isinstance(value, bool),
             'number': isinstance(value, (int, float)) and not isinstance(value, bool),
             'null': value is None}
    if kind and not valid[kind]:
        return [f'{path}: expected {kind}']
    errors = []
    if kind == 'object':
        properties = schema.get('properties', {})
        errors += [f'{path}: missing {key}' for key in schema.get('required', []) if key not in value]
        for key, item in value.items():
            if key in properties:
                errors += shape_errors(item, properties[key], f'{path}.{key}', definitions)
            elif schema.get('additionalProperties') is False:
                errors.append(f'{path}: unexpected {key}')
            elif isinstance(schema.get('additionalProperties'), dict):
                errors += shape_errors(item, schema['additionalProperties'], f'{path}.{key}', definitions)
    if kind == 'array':
        if 'maxItems' in schema and len(value) > schema['maxItems']:
            errors.append(f'{path}: expected at most {schema["maxItems"]} items')
        if 'minItems' in schema and len(value) < schema['minItems']:
            errors.append(f'{path}: expected at least {schema["minItems"]} items')
        for index, item in enumerate(value):
            errors += shape_errors(item, schema['items'], f'{path}[{index}]', definitions)
    return errors


def reachable(api_ids: list[str], modules: dict[str, dict[str, Any]]) -> set[str]:
    pending, seen = list(api_ids), set()
    while pending:
        key = pending.pop()
        if key in seen or key not in modules:
            continue
        seen.add(key)
        pending.extend(call['module_id'] for call in modules[key].get('calls', []))
    return seen


def validate_backend_ir(ir: dict[str, Any], schema: dict[str, Any], scenarios: set[str]) -> list[str]:
    """Require structurally valid, fully resolved graphs at commit/lowering boundaries."""
    errors = shape_errors(ir, IR_SCHEMA)
    if errors:
        return errors
    conflicts = ModuleConflictIndex([]).errors({'new_modules': ir['modules']}, [])
    return list(dict.fromkeys(graph_errors(ir) + contract_errors(ir, schema) + conflicts + semantic_type_errors(ir)))


def semantic_type_registry(ir: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Index used field identities by real types, including nested named definitions."""
    definitions = {row['id']: row for row in ir['types']}

    def signature(value: dict[str, Any], active: frozenset[str] = frozenset()) -> Any:
        kind = value['kind']
        if kind == 'scalar':
            return {'kind': kind, 'name': value['name']}
        if kind == 'array':
            return {'kind': kind, 'items': signature(value['items'], active)}
        key = value['type_id']
        if key in active or key not in definitions:
            return {'kind': 'named', 'type_id': key}
        definition = definitions[key]
        if definition['kind'] == 'enum':
            return {'kind': 'enum', 'values': sorted(definition['values'])}
        return {'kind': 'object', 'fields': sorted([
            {'name': field['name'], 'type': signature(field['type'], active | {key}),
             'required': field['required'], 'nullable': field['nullable']}
            for field in definition['fields']], key=lambda field: field['name'])}

    registry: dict[str, list[dict[str, Any]]] = {}
    visited_types: set[str] = set()

    def visit(value: Any, owner: str, location: str) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, owner, f'{location}[{index}]')
        elif isinstance(value, dict):
            if 'semantic_id' in value and isinstance(value.get('type'), dict):
                registry.setdefault(value['semantic_id'], []).append({
                    'owner_id': owner, 'location': location, 'type': value['type'],
                    'signature': signature(value['type'])})
            if value.get('kind') == 'named':
                key = value['type_id']
                if key in definitions and key not in visited_types:
                    visited_types.add(key)
                    visit(definitions[key], key, key)
            for key, item in value.items():
                visit(item, owner, f'{location}.{key}')
    for module in ir['modules']:
        visit(module, module['id'], module['id'])
    return registry


def semantic_type_summary(ir: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for semantic_id, occurrences in sorted(semantic_type_registry(ir).items()):
        types = {json.dumps(row['type'], sort_keys=True): row['type'] for row in occurrences}
        result.append({'semantic_id': semantic_id, 'types': list(types.values()),
                       'owner_ids': sorted({row['owner_id'] for row in occurrences})})
    return result


def semantic_type_errors(ir: dict[str, Any]) -> list[str]:
    errors = []
    for semantic_id, occurrences in semantic_type_registry(ir).items():
        groups: dict[str, list[str]] = {}
        for row in occurrences:
            key = json.dumps(row['signature'], sort_keys=True)
            groups.setdefault(key, []).append(row['location'])
        if len(groups) > 1:
            details = '; '.join(f'{kind} at {locations[:4]}' for kind, locations in groups.items())
            errors.append(f'SEMANTIC_TYPE_CONFLICT {semantic_id}: {details}. A value and a wrapper record '
                          'need different semantic IDs. If this is an intentional type change, declare '
                          'semantic_type_changes and explicitly revise every associated contract atomically.')
    return errors


def semantic_revision_errors(base: dict[str, Any], candidate: dict[str, Any], value: dict[str, Any]) -> list[str]:
    """A declared migration is atomic; no unchanged occurrence may keep the old type."""
    changes = value.get('semantic_type_changes', {})
    if not changes:
        return []
    errors = []
    before, after = semantic_type_registry(base), semantic_type_registry(candidate)
    definitions = {row['id']: row for row in candidate['types']}
    for key, target in changes.items():
        if key not in before:
            errors.append(f'semantic_type_changes: {key} is not an existing semantic ID; remove this declaration.')
            continue
        if not after.get(key):
            errors.append(f'semantic_type_changes: {key} must remain represented by the revised contracts.')
            continue
        probe = {'types': list(definitions.values()), 'modules': [
            {'id': 'migration', 'field': {'semantic_id': key, 'type': target}}]}
        wanted = semantic_type_registry(probe)[key][0]['signature']
        for row in after[key]:
            if row['signature'] != wanted:
                errors.append(f'Type migration {key}: {row["location"]} still has {row["type"]}; '
                              f'revise it to the declared {target} in the same reply.')
    revised = {row['id'] for row in value.get('updated_modules', [])}
    before_modules = {row['id']: row for row in base['modules']}
    after_modules = {row['id']: row for row in candidate['modules']}
    for owner in revised & before_modules.keys() & after_modules.keys():
        old = semantic_type_registry({'modules': [before_modules[owner]], 'types': base['types']})
        new = semantic_type_registry({'modules': [after_modules[owner]], 'types': candidate['types']})
        for key in (old.keys() & new.keys()) - changes.keys():
            old_signatures = {json.dumps(row['signature'], sort_keys=True) for row in old[key]}
            new_signatures = {json.dumps(row['signature'], sort_keys=True) for row in new[key]}
            if old_signatures != new_signatures:
                errors.append(f'Type revision {owner} also changes {key}; explicitly include that semantic ID '
                              'in semantic_type_changes or preserve its original type.')
    return errors


def named_references(value: Any) -> set[str]:
    if isinstance(value, list):
        return set().union(*(named_references(item) for item in value))
    if isinstance(value, dict):
        result = {value['type_id']} if value.get('kind') == 'named' else set()
        return result | set().union(*(named_references(item) for item in value.values()))
    return set()


def contract_errors(ir: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Check facts expressed by the IR, without guessing business implementations."""
    errors: list[str] = []
    tables = {row['name']: {column['name'] for column in row['columns']}
              for row in schema['tables']}
    modules = {row['id']: row for row in ir['modules']}

    def unique(values: list[str], owner: str) -> None:
        if len(set(values)) != len(values):
            errors.append(f'{owner}: duplicate entries; use unique names/IDs.')
        if any(not value.strip() for value in values):
            errors.append(f'{owner}: empty names/IDs are not allowed.')

    def fields(rows: list[dict[str, Any]], owner: str, *, request: bool = False) -> None:
        unique([f"{row['source']}:{row['name']}" if request else row['name'] for row in rows], owner + '.names')
        unique([row['semantic_id'] for row in rows], owner + '.semantic_ids')

    for row in ir['types']:
        if row['kind'] == 'object':
            fields(row['fields'], row['id'])
        else:
            unique(row['values'], row['id'] + '.values')
            if not row['values']:
                errors.append(f"{row['id']}: enum must contain values.")
    for row in ir['modules']:
        owner, kind = row['id'], row['kind']
        if not owner.split('::')[-1].startswith(kind + '.'):
            errors.append(f'{owner}: ID must use {kind}.Name matching its kind.')
        if not row['spec'].strip():
            errors.append(f'{owner}: spec must describe the responsibility and observable behavior.')
        fields(row.get('context', []), owner + '.context')
        inputs, outputs = ('request', 'response') if kind == 'API' else ('parameters', 'returns')
        fields(row[inputs], owner + '.' + inputs, request=kind == 'API')
        fields(row[outputs], owner + '.' + outputs)
        calls = row.get('calls', [])
        unique([call['id'] for call in calls], owner + '.calls')
        call_ids = {call['id'] for call in calls}
        ordering = {call['id']: call.get('after', []) for call in calls}
        for call in calls:
            target = modules.get(call['module_id'])
            target_kind = target['kind'] if target else call['module_id'].split('::')[-1].split('.')[0]
            allowed = {'API': {'FUNC'}, 'FUNC': {'FUNC', 'DB'}, 'DB': set()}[kind]
            if target_kind not in allowed:
                errors.append(f"{owner}: illegal {kind} -> {target_kind} call {call['module_id']}; allowed targets: {sorted(allowed)}.")
            if not call['purpose'].strip():
                errors.append(f"{owner}.{call['id']}: call purpose must describe delegated work.")
            missing = set(call.get('after', [])) - call_ids
            if missing:
                errors.append(f"{owner}.{call['id']}: after references unknown local calls {sorted(missing)}.")
        errors.extend(cycle_errors(ordering, owner + '.call ordering'))
        if kind == 'API':
            path = row['path']
            if not path.startswith('/') or any(char in path for char in ('?', '#', '{', '}', ' ')):
                errors.append(f'{owner}: path must be an absolute route with :parameter syntax and no query/hash.')
            placeholders = re.findall(r':([A-Za-z_][A-Za-z0-9_]*)', path)
            unique(placeholders, owner + '.path parameters')
            path_fields = [field for field in row['request'] if field['source'] == 'path']
            if set(placeholders) != {field['name'] for field in path_fields}:
                errors.append(f'{owner}: path request fields must exactly match route parameters {placeholders}.')
            if any(not field['required'] or field['nullable'] for field in path_fields):
                errors.append(f'{owner}: path fields must be required and nonnullable.')
            if row['method'] == 'GET' and any(field['source'] == 'body' for field in row['request']):
                errors.append(f'{owner}: GET cannot have body request fields.')
            if not 200 <= row['status_code'] <= 299:
                errors.append(f'{owner}: status_code describes success and must be 2xx; describe errors in spec.')
            if row['status_code'] == 204 and row['response']:
                errors.append(f'{owner}: status 204 cannot return response fields.')
        if kind == 'DB':
            unique([access['table'] for access in row['data_access']], owner + '.data_access tables')
            for access in row['data_access']:
                table = access['table']
                if table not in tables:
                    errors.append(f'{owner}: unknown data_access table {table}; use an existing schema table.')
                    continue
                for operation in ('read', 'create', 'update'):
                    unique(access[operation], f'{owner}.{table}.{operation}')
                    unknown = set(access[operation]) - tables[table]
                    if unknown:
                        errors.append(f'{owner}: {table}.{operation} references unknown columns {sorted(unknown)}.')
    errors.extend(cycle_errors({row['id']: named_references(row) for row in ir['types']}, 'named types'))
    return errors


def cycle_errors(edges: dict[str, Any], owner: str) -> list[str]:
    """Recursive named types cannot be expanded by the current lowering projection."""
    visiting, finished, errors = set(), set(), []
    def visit(key: str) -> None:
        if key in visiting:
            errors.append(f'{owner}: cyclic dependency at {key}.')
            return
        if key in finished or key not in edges:
            return
        visiting.add(key)
        for target in edges[key]:
            visit(target)
        visiting.remove(key)
        finished.add(key)
    for key in edges:
        visit(key)
    return errors


def layer_reference_errors(value: dict[str, Any], *, rid: str, kind: str,
                           base: dict[str, Any], loaded: dict[str, set[str]],
                           updated_ids: set[str] | None = None) -> list[str]:
    """Require full contracts before reuse and prevent silent type replacement."""
    errors = []
    existing_types = {row['id'] for row in base['types']}
    new_types = [row['id'] for row in value.get('new_types', [])]
    if len(set(new_types)) != len(new_types):
        errors.append('new_types contains duplicate IDs.')
    if set(new_types) & existing_types:
        errors.append(f'Cannot redefine existing types {sorted(set(new_types) & existing_types)}; reuse unchanged or use a new ID.')
    unloaded = named_references(value) - set(new_types) - loaded['requested_type_ids']
    if unloaded:
        errors.append(f'Named types lack loaded contracts: {sorted(unloaded)}. Request existing types or define new_types.')
    existing_modules = {row['id'] for row in base['modules']}
    for row in value['new_modules']:
        if '::' in row['id'] and not row['id'].startswith(rid + '::') and row['id'] not in (updated_ids or set()):
            errors.append(f"New module {row['id']} belongs to another requirement; reuse its exact ID instead of regenerating it.")
        for call in row.get('calls', []):
            target = call['module_id']
            if target in existing_modules and target not in loaded['requested_module_ids']:
                errors.append(f'{row["id"]}: referenced existing module {target} is not loaded; request its contract first.')
            elif target not in existing_modules and '::' in target and not target.startswith(rid + '::'):
                errors.append(f'{row["id"]}: cannot invent forward call {target} for another requirement.')
        for access in row.get('data_access', []):
            if access['table'] not in loaded['requested_tables']:
                errors.append(f'{row["id"]}: table {access["table"]} is not loaded; request an existing table before using its columns.')
    return errors


def graph_errors(ir: dict[str, Any], *, forward_kind: str | None = None) -> list[str]:
    modules = {row['id']: row for row in ir['modules']}
    types = {row['id']: row for row in ir['types']}
    errors = []
    if len(modules) != len(ir['modules']):
        errors.append('Backend module IDs must be unique')
    if len(types) != len(ir['types']):
        errors.append('Backend type IDs must be unique')
    for row in modules.values():
        for call in row.get('calls', []):
            key = call['module_id']
            if key not in modules and not (forward_kind and key.split('::')[-1].startswith(forward_kind + '.')):
                errors.append(f"Unresolved call: {row['id']} -> {key}. Define it in this layer or resolve it to a loaded existing module.")

    def check_types(value: Any, owner: str) -> None:
        if isinstance(value, list):
            for item in value:
                check_types(item, owner)
        elif isinstance(value, dict):
            if value.get('kind') == 'named' and value.get('type_id') not in types:
                errors.append(f"Unknown named type in {owner}: {value.get('type_id')}")
            for item in value.values():
                check_types(item, owner)
    for row in [*modules.values(), *types.values()]:
        check_types(row, row['id'])
    visiting, finished = set(), set()
    def visit(key: str) -> None:
        if key in visiting:
            errors.append(f'Cyclic backend call graph at {key}')
            return
        if key in finished or key not in modules:
            return
        visiting.add(key)
        for call in modules[key].get('calls', []):
            visit(call['module_id'])
        visiting.remove(key)
        finished.add(key)
    for key in modules:
        visit(key)
    return list(dict.fromkeys(errors))
