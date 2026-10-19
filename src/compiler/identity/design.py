"""Add identity storage to the same ER used by business modules."""
from __future__ import annotations

import copy


def identity_enabled(requirements):
    from .stage import IdentityDesignError
    design = requirements.get('identity_design')
    if not design or design.get('status') != 'RESOLVED':
        raise IdentityDesignError('Run and resolve the identity semantic design before lowering or implementation')
    return design.get('strategy') == 'local_password_cookie'


def install_identity(state, requirements, design):
    if design['status'] != 'RESOLVED':
        raise ValueError('Cannot install an unresolved identity design')
    if not design['strategy']:
        return {}
    proposal = design['proposal']
    subject, catalogue, assignment = proposal['subject'], proposal['roles'], proposal['assignment']
    model = requirements.get('bundle', {}).get('authorization', {}).get('model', {})
    roles = catalogue['definitions']
    known = {row['id'] for row in roles}
    root = requirements['root_id']
    user, role = subject['entity'], catalogue['entity']
    errors = []

    def entity(key, description):
        if key not in state.entities:
            state.apply_entities(root, {'new_entities': [{'key': key, 'description': description}]}, errors)

    def field(key, name, kind, description, *, nullable=False, properties=None):
        existing = state.entities[key]['fields'].get(name)
        if existing and existing['type'] != kind:
            raise ValueError(f'Identity field type conflicts with ER: {key}.{name}')
        declaration = {'name': name, 'type': kind, 'description': description,
                       'nullable': nullable, 'properties': properties or {}}
        if not existing:
            state._add_field(key, {**declaration, 'origin': 'SYSTEM', 'references': None}, root, errors)
        else:
            # Preserve published domain facts; only identity-owned additions are SYSTEM.
            existing['properties'].update(properties or {})
            declaration.update(nullable=existing['nullable'], properties=copy.deepcopy(existing['properties']),
                               description=existing['description'])
        state.reference_fields[f'{key}.{name}'] = declaration

    def relationship(key, parent, child, kind, *, pmax='one', cmax='one', required=True):
        if any(row['key'] == key for row in state.relationships.values()):
            raise ValueError(f'Reserved identity relationship already exists: {key}')
        row = {'key': key, 'parent': parent, 'child': child, 'type': kind,
               'parent_min': 0, 'parent_max': pmax, 'child_min': int(required), 'child_max': cmax,
               'description': f'Identity-owned association between {parent} and {child}.',
               'evidence_requirement_ids': [root]}
        state.apply_relationships(root, {'relationships': [row]}, errors)
        state.reference_relationships[key] = row
        return key

    entity(user, 'Application subject selected by evidence-backed IdentityIR.')
    entity(role, 'Role catalogue selected by evidence-backed IdentityIR.')
    login = subject['login_field']
    display = subject['display_field'] or login
    field(user, login, 'string', 'Unique login identifier.', nullable=False, properties={'unique': True})
    if display != login:
        field(user, display, 'string', 'Displayed subject name.', nullable=True)
    role_name = catalogue['name_field']
    role_key = catalogue['key_field'] or 'identity_role_key'
    field(role, role_name, 'string', 'Human-readable role name.')
    field(role, role_key, 'string', 'Canonical role ID from IdentityIR.', nullable=True, properties={'unique': True})
    if catalogue['binding'] == 'new' and catalogue['administrative_field']:
        field(role, catalogue['administrative_field'], 'boolean', 'Explicitly mapped descriptive administrative attribute.', properties={'default': False})
    credentials, sessions = 'identity_credential', 'identity_session'
    if credentials in state.entities or sessions in state.entities:
        raise ValueError('identity_credential and identity_session are reserved infrastructure entities')
    entity(credentials, 'Server-owned password credential and account state; never expose through business APIs.')
    entity(sessions, 'Server-owned opaque login session; token hashes only, never a client-supplied principal.')
    field(credentials, 'password_hash', 'string', 'Salted scrypt password encoding.')
    field(credentials, 'disabled', 'boolean', 'Account is prevented from authenticating.', properties={'default': False})
    field(credentials, 'team_id', 'string', 'Optional trusted team identifier for policy subject context.', nullable=True)
    for name, kind, description, props in (
        ('token_hash', 'string', 'SHA256 hash of an opaque session token.', {'unique': True}),
        ('created_at', 'integer', 'Session creation time in epoch milliseconds.', {}),
        ('expires_at', 'integer', 'Session expiration time in epoch milliseconds.', {}),
    ):
        field(sessions, name, kind, description, properties=props)
    relationship('identity_user_credential', user, credentials, 'ONE_TO_ONE')
    relationship('identity_user_session', user, sessions, 'ONE_TO_MANY', pmax='many')
    membership_key = assignment['relationship']
    if assignment['kind'] == 'new_many_to_many':
        relationship(membership_key, user, role, 'MANY_TO_MANY', pmax='many', cmax='many', required=False)
    bootstrap = requirements.get('bundle', {}).get('ui', {}).get('validation', {}).get('account', {})
    bootstrap_roles = bootstrap.get('roles', [])
    if not isinstance(bootstrap_roles, list) or set(bootstrap_roles) - known:
        raise ValueError('Validation account references unknown policy roles')
    if errors:
        raise ValueError('; '.join(errors))
    state.identity = {'ir_kind': 'identity', 'version': 1,
        'user_table': user, 'role_table': role, 'login_column': login, 'display_column': display,
        'role_name_column': role_name, 'role_key_column': role_key,
        'role_administrative_column': catalogue['administrative_field'],
        'credential_table': credentials, 'session_table': sessions, 'user_fk': user + '_id',
        'membership_table': membership_key + '_association', 'membership_user_fk': user + '_id',
        'membership_role_fk': role + '_id', 'system_entities': [credentials, sessions],
        'roles': [{'id': row['id'], 'name': row.get('name') or row['id'], 'aliases': row.get('aliases', []),
                   'administrative': any(permission['role_ref'] == row['id'] and permission['effect'] == 'allow'
                                         and 'admin' in permission['action_refs'] for permission in model.get('permissions', []))}
                  for row in roles],
        'role_bindings': copy.deepcopy(model.get('role_bindings', [])), 'bootstrap_roles': bootstrap_roles,
        'authentication_required_by_default': False}
    return copy.deepcopy(state.identity)
