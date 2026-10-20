// Compiler-owned identity interfaces. Roles are policy IDs, never display names.
export interface IdentitySubject {
  readonly id: string;
  readonly username: string;
  readonly display_name: string;
  readonly roles: readonly string[];
  readonly team_id?: string;
}

export interface IdentitySession {
  subject: IdentitySubject;
  csrf_token: string;
  expires_at: number;
}

export interface IdentityColumn {
  name: string;
  type: string;
  nullable: boolean;
  default: { kind: string; value: unknown } | null;
  generation: string;
}

export interface IdentityConfiguration {
  user_table: string;
  role_table: string;
  login_column: string;
  display_column: string;
  role_name_column: string;
  role_key_column: string;
  role_administrative_column: string;
  credential_table: string;
  session_table: string;
  user_fk: string;
  membership_table: string;
  membership_user_fk: string;
  membership_role_fk: string;
  user_columns: IdentityColumn[];
  role_columns: IdentityColumn[];
  roles: Array<{ id: string; name: string; aliases: string[]; administrative: boolean }>;
  role_bindings: Array<{ subject_ref: string; role_ref: string }>;
  bootstrap_roles: string[];
  bootstrap_account?: { username: string; password: string; roles: string[]; profile: Record<string, unknown> };
}
