"""Compiler authentication policy; source requirements remain unchanged."""
import copy

AUTHENTICATION_POLICY = {
    'id': 'account_password_v1', 'version': 1,
    'when': 'Requirements involve user authentication',
    'methods': ['password'], 'transport': 'cookie',
    'deferred': ['self_registration', 'registration_sms_verification', 'authentication_sms_delivery', 'wechat_authorization', 'sso', 'external_authentication'],
    'preserve': ['subject_binding', 'role_ids', 'membership', 'data_scope', 'business_authorization'],
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
Preserve actual subject/role bindings, membership, organization/tenant boundaries, ownership and
permission rules. This policy never grants access or converts scoped roles into global roles.
Use the compiler-owned identity store/endpoints/client/login gate. Bootstrap the initial account
through that infrastructure, not through a duplicate login/session/password mechanism. The initial
account is admin/admin123456; use only published validation roles, otherwise no roles. Required
profile values must be explicit in identity design. Do not expose a default password in public DTOs.
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
