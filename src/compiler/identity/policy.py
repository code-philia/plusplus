"""Compiler authentication policy; source requirements remain unchanged."""
import copy

AUTHENTICATION_POLICY = {
    'id': 'account_password_v1', 'version': 2,
    'when': 'Requirements involve user authentication',
    'methods': ['password'], 'transport': 'cookie',
    'deferred': ['self_registration', 'registration_sms_verification', 'authentication_sms_delivery', 'wechat_authorization', 'sso', 'external_authentication'],
    'preserve': ['subject_binding', 'role_ids', 'membership', 'data_scope', 'business_authorization'],
    'default_administrator': {
        'identity': 'Server-verified subject UUID of the configured bootstrap administrator account',
        'permissions': 'All business resources and actions in the generated application',
        'data_scope': 'All application records; bypass role, ownership, team and assignment restrictions',
        'precedence': 'Explicit compiler override of source authorization omissions and deny rules for this subject only',
    },
    'bootstrap': {'username': 'admin', 'password': 'admin123456',
                  'roles': 'Published validation-account roles, otherwise no roles',
                  'profile': 'Explicit domain profile from identity design or bootstrap configuration'},
}

POLICY_INSTRUCTIONS = '''
## Compiler authentication policy (takes precedence over source authentication mechanisms)
First decide from requirement evidence whether authentication is needed. Do not add login to an
application that does not require it. Whenever authentication is required, generate local account/
password authentication with Cookie sessions, even if the source mentions SMS, WeChat, SSO, external
identity or leaves the original transport unspecified. This is an explicit user-selected generation
policy, not a claim that the source system uses passwords. Preserve original evidence/provenance.
Defer self-registration, authentication/registration SMS checks/sending, WeChat authorization and external-provider login; do not
implement their forms, APIs, SDKs, verification codes or notifications in this version. Administrator
creation of internal users may use the shared provisioning functions without SMS. Keep unrelated
mobile/business operations and non-authentication business notifications in scope; source authentication mechanisms do not block those operations.
Preserve actual subject/role bindings and membership. For ordinary users, preserve source
organization/tenant boundaries, ownership and permission rules. The default administrator exception
below is an explicit user-selected authorization override, not a source-derived role assumption.
Use the compiler-owned identity store/endpoints/client/login gate. Bootstrap the initial account
through that infrastructure, not through a duplicate login/session/password mechanism. The initial
account is admin/admin123456; use only published validation roles, otherwise no roles. Required
profile values must be explicit in identity design. Do not expose a default password in public DTOs.

## 默认管理员权限 / Default administrator authorization
默认管理员拥有系统内全部业务资源、全部操作和全部数据的权限。
After authenticating the configured bootstrap administrator, grant access to every application
feature, page, menu, button and business API, including user/role administration, exports and reports.
API/FUNC contracts must state this administrator branch explicitly. DB contracts must return and
mutate the full applicable dataset for this administrator without own/team/assigned or role filters.
Evaluate this explicit administrator grant before ordinary policy rules: missing allow entries,
partial source policies and source deny rules must not block this administrator. Other users retain
their original authorization rules, deny precedence and data filters. Do not reinterpret the source
resource all_system_features as a wildcard or grant this exception to every role named admin.
Identify the default administrator through its persisted bootstrap account subject UUID and trusted
server session. Never grant privileges from client-supplied usernames, role flags, headers, query
parameters or body fields; an account merely named admin or carrying an admin display label is not
sufficient evidence. Propagate a server-verified administrator decision through explicit contracts.
Frontend navigation and action visibility must agree with backend authorization for this account.
Full permissions do not bypass authentication, disabled-account/session checks, input validation,
referential integrity, business invariants, required confirmations or transaction consistency.
'''


def authentication_policy():
    return copy.deepcopy(AUTHENTICATION_POLICY)


def normalize_authentication(proposal):
    """Only normalize the mechanism, never identity/authorization bindings or unresolved gaps."""
    value = copy.deepcopy(proposal)
    if not isinstance(value, dict) or value.get('decision') != 'required':
        return value, []
    auth = value.get('authentication')
    if not isinstance(auth, dict) or not isinstance(auth.get('evidence_ids'), list):
        return value, []
    original = copy.deepcopy(auth)
    auth.update(methods=['password'], transport='cookie')
    if 'compiler:authentication_policy' not in auth['evidence_ids']:
        auth['evidence_ids'].append('compiler:authentication_policy')
    return value, [{'code': 'AUTHENTICATION_POLICY_APPLIED', 'policy_id': AUTHENTICATION_POLICY['id'],
                    'source_authentication': original, 'effective_methods': ['password'], 'effective_transport': 'cookie'}]
