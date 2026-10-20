"""Semantic identity analysis, validation and explicit capability selection."""
from __future__ import annotations
import copy
import hashlib
import json
import os
import re
from pathlib import Path
from compiler.backend_design.validation import shape_errors
from utils.jsonio import write_json_atomic
from .contracts import SCHEMA, INSTRUCTIONS
from .policy import authentication_policy, normalize_authentication

class IdentityDesignError(ValueError):
    """A blocked identity decision must never fall back to an anonymous application."""


def evidence_registry(requirements, state):
    registry = {'compiler:authentication_policy': authentication_policy()}
    for rid, node in requirements.get('nodes', {}).items():
        registry['requirement:' + rid] = {key: copy.deepcopy(node.get(key)) for key in
            ('name', 'description', 'scenarios', 'pages', 'parent_id')}
    for key, entity in state.entities.items():
        registry['entity:' + key] = {'description': entity['description'], 'requirement_ids': entity['requirement_ids']}
        for name, field in entity['fields'].items():
            registry[f'field:{key}.{name}'] = copy.deepcopy(field)
    for row in state.relationships.values():
        registry['relationship:' + row['key']] = copy.deepcopy(row)
    bundle = requirements.get('bundle', {})
    model = bundle.get('authorization', {}).get('model', {})
    if model:
        registry['authorization:catalog'] = copy.deepcopy(model)
    ui = bundle.get('ui', {})
    for key in ('pages', 'states'):
        rows = ui.get(key, [])
        if isinstance(rows, dict):
            rows = [dict(value, id=name) for name, value in rows.items()]
        for index, row in enumerate(rows):
            if isinstance(row, dict):
                registry[f'{key}:{row.get("id", index)}'] = copy.deepcopy(row)
    # Validation account secrets are deliberately excluded from model input/artifacts.
    return registry


def validate_proposal(value, requirements, state, evidence):
    errors = shape_errors(value, SCHEMA)
    if errors:
        return errors
    def citations(row, label, required=True):
        ids = row['evidence_ids']
        if required and not ids:
            errors.append(label + ': evidence_ids are required')
        for key in ids:
            if key not in evidence:
                errors.append(label + ': unknown evidence ' + key)
    citations(value, 'decision')
    if not value['rationale'].strip():
        errors.append('Explain the identity decision')
    for row in value['gaps']:
        citations(row, 'gap', False)
    policy = requirements.get('bundle', {}).get('authorization', {}).get('model', {})
    if value['decision'] == 'not_required':
        if any(row['blocking'] for row in value['gaps']):
            errors.append('not_required cannot bypass blocking identity gaps')
        if any(policy.get(key) for key in ('roles', 'permissions', 'role_bindings')):
            errors.append('Identity cannot be disabled while policy roles/permissions/bindings exist')
        if (value['authentication']['methods'] or value['subject']['binding'] != 'none'
                or value['roles']['binding'] != 'none' or value['assignment']['kind'] != 'none'):
            errors.append('not_required must have empty methods and none subject/roles/assignment')
        return errors
    for label in ('authentication', 'subject', 'roles', 'assignment', 'boundary'):
        citations(value[label], label)
    if value['decision'] == 'unresolved':
        if not any(row['blocking'] for row in value['gaps']):
            errors.append('unresolved needs a blocking gap')
        return errors
    if any(row['blocking'] for row in value['gaps']):
        errors.append('required decision cannot contain blocking gaps; use unresolved')
    if select_strategy(value)[1]:
        return errors
    if ('bootstrap_profile' not in value
            and requirements.get('bundle', {}).get('identity_bootstrap') is None):
        errors.append('Provide bootstrap_profile as an encoded JSON object for required domain profile fields; the compiler initializes admin/admin123456')
    subject, roles = value['subject'], value['roles']
    for label, row in (('subject', subject), ('roles', roles)):
        if row['binding'] not in ('existing', 'new'):
            errors.append(label + ': this runtime requires an explicit existing/new entity binding')
            continue
        if not re.fullmatch(r'[a-z][a-z0-9_]*', row['entity']):
            errors.append(label + ': entity must be a canonical ER symbol')
        if row['binding'] == 'existing' and row['entity'] not in state.entities:
            errors.append(label + ': unknown entity ' + row['entity'])
        if row['binding'] == 'new':
            if row['entity'] in state.entities:
                errors.append(label + ': new entity already exists')
            if not any(key.startswith('requirement:') for key in row['evidence_ids']):
                errors.append(label + ': creating an entity requires requirement evidence')
    for label, row in (('subject', subject), ('roles', roles)):
        if row['binding'] == 'existing' and 'entity:' + row['entity'] not in row['evidence_ids']:
            errors.append(label + ': cite the selected entity explicitly')
    if subject['entity'] == roles['entity']:
        errors.append('Subject and role catalogue must have separate entity bindings')
    for label, row, names in (('subject', subject, ('login_field', 'display_field')),
                              ('roles', roles, ('name_field', 'key_field', 'administrative_field'))):
        fields = state.entities.get(row['entity'], {}).get('fields', {})
        for key in names:
            name = row[key]
            if not name:
                continue
            if not re.fullmatch(r'[a-z][a-z0-9_]*', name) or name == 'id':
                errors.append(label + '.' + key + ': invalid/reserved field')
                continue
            expected = 'boolean' if key == 'administrative_field' else 'string'
            if name in fields and f'field:{row["entity"]}.{name}' not in row['evidence_ids']:
                errors.append(label + '.' + key + ': cite the selected field explicitly')
            if name in fields and fields[name]['type'] != expected:
                errors.append(label + '.' + key + ': incompatible field type')
            elif row['binding'] == 'existing' and name not in fields:
                if key != 'login_field' or not subject['add_login_field']:
                    errors.append(label + '.' + key + ': field does not exist')
    if not subject['login_field'] or not roles['name_field']:
        errors.append('Subject login_field and role name_field are required')
    if subject['add_login_field'] and not any(key.startswith('requirement:') for key in subject['evidence_ids']):
        errors.append('Adding a login field needs requirement evidence')
    login = state.entities.get(subject['entity'], {}).get('fields', {}).get(subject['login_field'])
    if login and not login.get('properties', {}).get('unique'):
        errors.append('Existing login identifier lacks a globally unique constraint; review domain ER instead of silently adding one')
    role_fields = state.entities.get(roles['entity'], {}).get('fields', {})
    if roles['binding'] == 'existing' and not roles['key_field'] and 'identity_role_key' in role_fields:
        errors.append('Reserved canonical role column already exists; explicitly bind and review it')
    key_field = role_fields.get(roles['key_field'])
    if key_field and not key_field.get('properties', {}).get('unique'):
        errors.append('Existing canonical role key needs a unique constraint')
    if (roles['key_field'] or 'identity_role_key') == roles['name_field']:
        errors.append('Role display name and canonical ID must use distinct fields')
    definitions = roles['definitions']
    ids = [row['id'] for row in definitions]
    if len(set(ids)) != len(ids) or any(not key for key in ids):
        errors.append('Role IDs must be nonempty and unique')
    for row in definitions:
        citations(row, 'role:' + row['id'])
    if policy:
        expected = [{key: row.get(key, []) if key == 'aliases' else row.get(key) for key in ('id', 'name', 'aliases')}
                    for row in policy.get('roles', [])]
        actual = [{key: row[key] for key in ('id', 'name', 'aliases')} for row in definitions]
        if sorted(expected, key=lambda row: row['id']) != sorted(actual, key=lambda row: row['id']):
            errors.append('Role definitions must preserve the complete policy catalogue exactly')
    if value.get('bootstrap_profile') is not None:
        from .design import bootstrap_profile_errors
        try:
            profile = json.loads(value['bootstrap_profile'])
        except (ValueError, TypeError):
            errors.append('bootstrap_profile must encode a JSON object')
        else:
            errors.extend(bootstrap_profile_errors(state, subject['entity'], subject['login_field'], profile))
    allowed_values = (key_field or {}).get('properties', {}).get('enum')
    if allowed_values and not set(ids) <= set(allowed_values):
        errors.append('Canonical role IDs conflict with existing field enum')
    for binding in policy.get('role_bindings', []):
        if binding.get('role_ref') not in ids or not isinstance(binding.get('subject_ref'), str):
            errors.append('Policy role binding references an invalid subject or role')
    assignment = value['assignment']
    if assignment['kind'] == 'many_to_many':
        if 'relationship:' + assignment['relationship'] not in assignment['evidence_ids']:
            errors.append('Cite the exact selected membership relationship')
        relation = next((row for row in state.relationships.values() if row['key'] == assignment['relationship']), None)
        if not relation or relation['type'] != 'MANY_TO_MANY' or {relation['parent'], relation['child']} != {subject['entity'], roles['entity']}:
            errors.append('Selected membership must be an existing M:N between the exact subject and role entities')
    if assignment['kind'] == 'new_many_to_many':
        if not re.fullmatch(r'[a-z][a-z0-9_]*', assignment['relationship']):
            errors.append('New membership needs a canonical relationship key')
        if any(row['key'] == assignment['relationship'] for row in state.relationships.values()):
            errors.append('New membership relationship already exists')
        if not any(key.startswith('requirement:') for key in assignment['evidence_ids']):
            errors.append('New membership requires requirement evidence')
        if any(row['type'] == 'MANY_TO_MANY' and {row['parent'], row['child']} == {subject['entity'], roles['entity']}
               for row in state.relationships.values()):
            errors.append('Existing subject-role membership must be reused or ambiguity resolved')
    return errors


def select_strategy(value):
    """Recognize unsupported semantics; never flatten or substitute a weaker scheme."""
    if value['decision'] == 'not_required':
        return '', []
    problems = []
    if value['decision'] == 'unresolved':
        return '', ['Unresolved identity semantics: ' + row['message'] for row in value['gaps'] if row['blocking']]
    auth, boundary = value['authentication'], value['boundary']
    if auth['methods'] != ['password'] or auth['transport'] != 'cookie':
        problems.append('Unsupported authentication strategy: ' + ','.join(auth['methods']) + '/' + auth['transport'])
    if boundary['kind'] != 'global' or boundary['account_namespace'] != 'global':
        problems.append('Scoped/ambiguous identity boundary requires a scope-aware runtime; global roles will not be generated')
    if value['roles']['binding'] not in ('existing', 'new'):
        problems.append('A roleless or unresolved role catalogue needs another runtime strategy')
    if value['assignment']['kind'] not in ('many_to_many', 'new_many_to_many'):
        problems.append('Unsupported role storage: ' + value['assignment']['kind'])
    return ('local_password_cookie' if not problems else ''), problems


def design_identity(model, artifact_root, requirements, state, *, reviewed_proposal=None, domain_status='PROPOSED'):
    root = Path(artifact_root) / 'design/identity'
    evidence = evidence_registry(requirements, state)
    source = reviewed_proposal if reviewed_proposal is not None else requirements.get('bundle', {}).get('identity_proposal')
    domain_er = state.to_er_model()
    domain_er['status'] = domain_status
    requirements['authentication_policy'] = authentication_policy()
    context = {'evidence': evidence, 'domain_er': domain_er, 'authentication_policy': authentication_policy(),
               'bootstrap_configuration_supplied': requirements.get('bundle', {}).get('identity_bootstrap') is not None}
    fingerprint = hashlib.sha256(json.dumps({'context': context, 'proposal': source}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    write_json_atomic(root / 'domain_er.json', domain_er)
    write_json_atomic(root / 'evidence.json', evidence)
    write_json_atomic(root / 'input.json', {'input_sha256': fingerprint, **context})
    # Set a blocking marker before any model call; generic pipeline recovery cannot bypass it.
    requirements['identity_design'] = {'status': 'UNRESOLVED', 'input_sha256': fingerprint,
                                       'diagnostics': ['Identity semantic analysis has not completed']}
    write_json_atomic(Path(artifact_root) / 'preprocessing/requirement_ir.json', requirements)
    write_json_atomic(root / 'contract.json', {'status': 'UNRESOLVED', 'storage': {}, 'runtime': {}})
    feedback = []
    value, raw_value, normalizations = None, None, []
    try:
        attempts = max(1, min(int(os.getenv('MODEL_ATTEMPT', '5')), 20))
    except ValueError:
        attempts = 5
    if source is not None:
        attempts = 1
    for attempt in range(attempts):
        try:
            raw_value = copy.deepcopy(source) if source is not None else model.generate_json(
                schema_name='arc_identity_semantics', instructions=INSTRUCTIONS,
                input_payload={**context, 'previous_validation_errors': feedback}, output_schema=SCHEMA)
            value, normalizations = normalize_authentication(raw_value)
            feedback = validate_proposal(value, requirements, state, evidence)
        except Exception as exc:
            feedback = [f'Identity semantic analysis failed: {type(exc).__name__}: {exc}']
        write_json_atomic(root / f'attempt-{attempt + 1}.json', {'source_proposal': raw_value, 'proposal': value, 'normalizations': normalizations, 'errors': feedback})
        if not feedback:
            break
    strategy, capabilities = select_strategy(value) if not feedback else ('', [])
    diagnostics = feedback + capabilities
    status = ('INVALID' if feedback else 'UNRESOLVED' if value['decision'] == 'unresolved'
              else 'UNSUPPORTED' if capabilities else 'RESOLVED')
    result = {'ir_kind': 'identity_design', 'version': 1, 'status': status,
              'input_sha256': fingerprint, 'origin': 'reviewed_binding' if reviewed_proposal is not None else 'bundle_binding' if source is not None else 'model',
              'strategy': strategy, 'proposal': value, 'source_proposal': raw_value,
              'authentication_policy': authentication_policy(), 'normalizations': normalizations, 'diagnostics': diagnostics}
    requirements['identity_design'] = result
    write_json_atomic(root / 'identity_ir.json', result)
    write_json_atomic(root / 'report.json', {key: value for key, value in result.items() if key not in ('proposal', 'source_proposal')})
    write_json_atomic(Path(artifact_root) / 'preprocessing/requirement_ir.json', requirements)
    if status != 'RESOLVED':
        raise IdentityDesignError('; '.join(diagnostics) + '. Review .arc/design/identity/identity_ir.json')
    return result


def compile_identity(model, artifact_root, requirements, state, *, reviewed_proposal=None, domain_status='PROPOSED'):
    from .design import install_identity
    from .lowering import public_contract
    result = design_identity(model, artifact_root, requirements, state, reviewed_proposal=reviewed_proposal, domain_status=domain_status)
    try:
        install_identity(state, requirements, result)
    except Exception as exc:
        result.update(status='INVALID', diagnostics=[f'Identity storage binding failed: {exc}'])
        write_json_atomic(Path(artifact_root) / 'design/identity/identity_ir.json', result)
        write_json_atomic(Path(artifact_root) / 'design/identity/report.json',
                          {key: value for key, value in result.items() if key not in ('proposal', 'source_proposal')})
        write_json_atomic(Path(artifact_root) / 'preprocessing/requirement_ir.json', requirements)
        raise IdentityDesignError(result['diagnostics'][0]) from exc
    write_json_atomic(Path(artifact_root) / 'design/identity/contract.json', {
        'design': result, 'storage': state.identity, 'runtime': public_contract(state.identity)})
    return result
