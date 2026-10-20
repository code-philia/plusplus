"""Evidence-backed semantic identity decision, independent of SQL/runtime names."""
def obj(**properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
def arr(items):
    return {'type': 'array', 'items': items}
def enum(*values):
    return {'type': 'string', 'enum': list(values)}
S = {'type': 'string'}
E = arr(S)
from .policy import POLICY_INSTRUCTIONS

SCHEMA = obj(
    decision=enum('required', 'not_required', 'unresolved'), rationale=S, evidence_ids=E,
    authentication=obj(methods=arr(enum('password', 'sms_otp', 'email_otp', 'oidc', 'saml', 'oauth', 'webauthn', 'external', 'unknown')),
                       transport=enum('cookie', 'bearer', 'external', 'unknown'), evidence_ids=E),
    subject=obj(binding=enum('existing', 'new', 'none', 'unresolved'), entity=S, login_field=S,
                display_field=S, add_login_field={'type': 'boolean'}, evidence_ids=E),
    roles=obj(binding=enum('existing', 'new', 'none', 'unresolved'), entity=S, name_field=S,
              key_field=S, administrative_field=S, evidence_ids=E,
              definitions=arr(obj(id=S, name=S, aliases=E, evidence_ids=E))),
    assignment=obj(kind=enum('many_to_many', 'new_many_to_many', 'join_entity', 'subject_field', 'none', 'unresolved'),
                   relationship=S, entity=S, field=S, evidence_ids=E),
    boundary=obj(kind=enum('global', 'organization', 'project', 'tenant', 'other', 'unresolved'),
                 entity=S, account_namespace=enum('global', 'scoped', 'unresolved'), evidence_ids=E),
    gaps=arr(obj(code=S, message=S, blocking={'type': 'boolean'}, evidence_ids=E)))

INSTRUCTIONS = """
Design an IdentityIR from the supplied evidence registry and finalized domain ER. Every
material binding cites exact registry evidence_ids. Existing bindings must cite the selected
entity, each selected field and the selected membership relationship explicitly. Names are not semantic evidence: do
not assume User, Role, account_name or is_admin. Infer the actor, login identifier, role
catalogue and assignment from descriptions, relations, requirements, policy and UI contracts.
Do not cite an unrelated requirement to justify a guess. A blank validation password proves
nothing about authentication. Recognize password, SMS/OTP, external identity, SSO and multiple
methods in the source evidence, but apply compiler:authentication_policy to the generated
authentication: password plus Cookie. Do not treat the original authentication method or
unspecified original transport as a blocking gap under this explicit policy.
Distinguish global role membership from organization/project/tenant scoped assignments and
scoped account namespaces. Preserve ambiguity as unresolved and list blocking gaps.
Required: provide authentication, subject, roles, assignment and boundary evidence separately.
An explicit new binding needs requirement evidence; never invent fallback domain entities.
Existing login fields must be string and globally unique for a global password account.
add_login_field is true only when requirements establish a new login identifier. An empty
display_field means display the login. Empty key_field on roles requests a technical canonical
policy role ID column; existing key_field must already store exact policy IDs. Never reuse a
human label or an enum of other values as canonical IDs. name_field and key_field must differ.
administrative_field is an optional explicit boolean domain attribute, not an authority check.
Preserve policy roles exactly, including IDs, labels and aliases. Without a policy, finite
role definitions need requirement or enum evidence. Do not invent an administrator role.
Represent join entities and scalar role assignments honestly; runtime strategy selection is
performed afterwards. Existing M:N relationship must be explicitly selected, not guessed.
not_required is valid only when the entire application has no identity requirement, with all
bindings none, methods empty and no policy roles, permissions or subject bindings. Explain why.
When decision=required and no explicit bootstrap file is supplied, return bootstrap_profile as
an encoded JSON object containing concrete values for required subject profile fields, excluding
id and login_field. These are synthetic first-version fixture values, not extracted user data.
Use existing field types/enums/constraints and cite selected profile fields in subject.evidence_ids.
Do not invent fields or roles. The compiler supplies the admin login/password separately.
Use empty strings for inapplicable fields. Do not include extracted account secrets.
""" + POLICY_INSTRUCTIONS

# Optional for older reviewed bindings; new model decisions supply required fixture values here.
SCHEMA['properties']['bootstrap_profile'] = S
