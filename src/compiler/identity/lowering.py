"""Generate fixed identity modules against the actual relational mapping."""
from __future__ import annotations

import copy
import json
from pathlib import Path

ASSETS = Path(__file__).parent / 'assets'
from .policy import POLICY_INSTRUCTIONS, authentication_policy

BACKEND_FILES = ('types.ts', 'password.ts', 'store.ts', 'http.ts')

DESIGN_INSTRUCTIONS = POLICY_INSTRUCTIONS + """
When identity_contract is supplied, identity is compiler-owned existing infrastructure.
Reuse its domain subject UUID, canonical role IDs, server context, provisioning functions,
fixed HTTP endpoints and browser client. Do not create another user/credential/session store,
duplicate /api/auth routes or treat supplied body/header/query roles or subject IDs as trusted
identity. Caller IDs needed by business functions must come from request.identity at the API
boundary. Keep credentials, session hashes and internal identity columns out of public DTOs.
Reserve identity-owned storage and integrate its service functions rather than designing raw
password/session DB modules. Authentication is not authorization: explicitly preserve the
requirement's role/resource/action and own/team/assigned restrictions for ordinary users, and
the explicit full-access branch for the server-verified bootstrap administrator, in business contracts.
Do not invent backend module IDs for the fixed identity endpoints; they are an external
infrastructure capability, invoked through the supplied client/service contracts.
"""


def public_contract(identity):
    if not identity:
        return {}
    return {'authentication_policy': {key: value for key, value in authentication_policy().items() if key != 'bootstrap'}, 'subject': {'id': f'The domain {identity["user_table"]}.id UUID; never a name or client payload.',
                        'roles': 'Canonical policy role IDs loaded from database and published bindings.',
                        'team_id': 'Optional trusted team ID from the credential record.'},
        'routes': [{'method': 'POST', 'path': '/api/auth/login', 'body': ['username', 'password'],
                    'response': ['subject', 'csrf_token', 'expires_at']},
                   {'method': 'GET', 'path': '/api/auth/me', 'response': ['subject', 'csrf_token', 'expires_at']},
                   {'method': 'POST', 'path': '/api/auth/logout', 'header': 'X-CSRF-Token', 'status': 204}],
        'transport': 'Opaque HttpOnly same-site session cookie; never store tokens in localStorage.',
        'backend': {'module': 'backend/src/identity/http.ts',
                    'middleware': ['identityContext', 'requireIdentity', 'requireCsrf'],
                    'subject': 'request.identity (server-constructed); requireIdentity rejects anonymous requests.'},
        'provisioning': {'module': 'backend/src/identity/store.ts',
            'functions': ['provisionIdentityAccount', 'setIdentityPassword', 'setIdentityRoles', 'setIdentityDisabled'],
            'restriction': 'Server-side functions only; business routes must authorize administration before calling.'},
        'login_ui': {'module': 'frontend/src/identity/login.tsx', 'component': 'IdentityGate',
                     'behavior': 'Restore current session; show username/password login when anonymous; provide logout. Reuse this fixed UI rather than adding registration/OTP/external login.'},
        'frontend': {'module': 'frontend/src/identity/client.ts',
                     'functions': ['login', 'logout', 'currentIdentity', 'authenticatedFetch']},
        'reserved_path': '/api/auth',
        'obligations': ['Preserve installIdentity(app) before business routes and bootstrapIdentityFromEnv at startup.',
                        'Preserve the IdentityGate wrapper around application content; reuse its login/current-user/logout UI.',
                        'Reuse this infrastructure; do not generate duplicate login/session/password mechanisms.',
                        'Business authorization and resource ownership filters are separate from authentication.',
                        'Never expose credential/session tables through APIs, DTOs or frontend data models.']}


def backend_sources(schema):
    identity = schema.get('identity')
    if not identity:
        return {}
    tables = {row['name']: row for row in schema['tables']}
    protected = {identity[key] for key in ('user_table', 'role_table', 'credential_table', 'session_table', 'membership_table')}
    if protected - tables.keys():
        raise ValueError('Identity storage cannot be partially lowered: ' + ', '.join(sorted(protected - tables.keys())))
    for table_key, column_key, target_key in (
        ('credential_table', 'user_fk', 'user_table'), ('session_table', 'user_fk', 'user_table'),
        ('membership_table', 'membership_user_fk', 'user_table'), ('membership_table', 'membership_role_fk', 'role_table'),
    ):
        table = tables[identity[table_key]]
        columns = {row['name']: row for row in table['columns']}
        if identity[column_key] not in columns:
            raise ValueError(f'Identity FK mapping is missing: {table["name"]}.{identity[column_key]}')
        constraints = [*table['constraints'], *(item for col in table['columns'] for item in col['constraints'])]
        if not any(row['kind'] == 'FOREIGN_KEY' and row.get('referenced_table') == identity[target_key]
                   and row.get('referenced_columns') == ['id'] and
                   (row.get('columns') == [identity[column_key]] or row in columns[identity[column_key]]['constraints'])
                   for row in constraints):
            raise ValueError(f'Identity FK no longer references the domain subject/role: {table["name"]}')
    config = copy.deepcopy(identity)
    for key in ('ir_kind', 'version', 'system_entities', 'authentication_required_by_default'):
        config.pop(key, None)
    for kind in ('user', 'role'):
        config[kind + '_columns'] = [{key: row[key] for key in ('name', 'type', 'nullable', 'default', 'generation')}
                                    for row in tables[identity[kind + '_table']]['columns']]
    files = {'backend/src/identity/' + name: (ASSETS / name).read_text(encoding='utf-8') for name in BACKEND_FILES}
    files['backend/src/identity/config.ts'] = ('// Compiler-owned identity mapping; optional explicitly configured bootstrap account.\n'
        'import type { IdentityConfiguration } from "./types.js";\n'
        'export const identityConfig: IdentityConfiguration = ' + json.dumps(config, ensure_ascii=False, indent=2) + ';\n')
    return files


def frontend_sources():
    return {'frontend/src/identity/' + name: (ASSETS / name).read_text(encoding='utf-8')
            for name in ('client.ts', 'login.tsx')}


def infrastructure_file(path):
    return path.startswith(('backend/src/identity/', 'frontend/src/identity/')) or path == 'frontend/src/navigation/runtime.tsx'


def integration_errors(root, *, require_wiring=True):
    """Keep model repair from dropping the installed identity runtime."""
    import re
    from utils.jsonio import read_json
    from compiler.lowering.files import digest
    root = Path(root).resolve()
    if not read_json(root / '.arc/lowering/database/plan.json', {}).get('identity'):
        return []
    errors = []
    for stage in ('database', 'frontend'):
        manifest = read_json(root / '.arc/lowering' / stage / 'manifest.json', {}).get('files', {})
        for name, expected in manifest.items():
            if infrastructure_file(name):
                path = (root / name).resolve()
                if not path.is_relative_to(root) or not path.is_file() or digest(path.read_text(encoding='utf-8')) != expected:
                    errors.append('Compiler-owned identity source changed or is missing: ' + name)
    if require_wiring:
        checks = {'backend/src/app.ts': (r'^\s*installIdentity\(app\);',),
                  'backend/src/server.ts': (r'^\s*initializeIdentity\(client\.connection\);',
                                            r'^\s*bootstrapIdentityFromEnv\(client\.connection\);')}
        for name, patterns in checks.items():
            content = (root / name).read_text(encoding='utf-8') if (root / name).is_file() else ''
            if any(len(re.findall(pattern, content, re.MULTILINE)) != 1 for pattern in patterns):
                errors.append('Preserve the compiler-owned identity integration calls in ' + name)
        app = (root / 'backend/src/app.ts').read_text(encoding='utf-8') if (root / 'backend/src/app.ts').is_file() else ''
        mount = re.search(r'^\s*installIdentity\(app\);', app, re.MULTILINE)
        route = re.search(r'\bapp\.(?:get|post|put|patch|delete|all|route)\s*\(', app)
        if mount and route and mount.start() > route.start():
            errors.append('Install identity before application route handlers')
    return errors
