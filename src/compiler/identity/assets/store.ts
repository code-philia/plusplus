// Compiler-owned identity persistence. Provisioning functions are trusted server APIs, not HTTP handlers.
import type Database from "better-sqlite3";
import { createHash, randomBytes, randomUUID } from "node:crypto";
import { performance } from "node:perf_hooks";
import { sqliteDatabase } from "../db/client.js";
import { identityConfig as config } from "./config.js";
import { hashIdentityPassword, validPasswordInput, verifyIdentityPassword } from "./password.js";
import type { IdentityColumn, IdentitySession, IdentitySubject } from "./types.js";

type Connection = Database.Database;
type Row = Record<string, unknown>;
const quote = (value: string): string => '"' + value.replaceAll('"', '""') + '"';
const table = (value: string): string => quote(value);
const tokenHash = (token: string): string => createHash("sha256").update(token).digest("hex");
const csrfToken = (token: string): string => createHash("sha256").update("arc-identity-csrf\0" + token).digest("hex");
// Security expiry is independent of an optional frozen application/validation Date clock.
export const identityNow = (): number => Math.floor(performance.timeOrigin + performance.now());

function parseObject(value: string | undefined, setting: string): Row {
  if (!value) return {};
  const result: unknown = JSON.parse(value);
  if (!result || typeof result !== "object" || Array.isArray(result)) throw new Error(setting + " must be a JSON object");
  return result as Row;
}

function validateRoles(roles: readonly string[]): string[] {
  if (!Array.isArray(roles) || roles.some(role => !config.roles.some(known => known.id === role))) {
    throw new Error("Unknown canonical identity role ID");
  }
  return [...new Set(roles)];
}

function insertRow(connection: Connection, name: string, columns: IdentityColumn[], values: Row): void {
  if (Object.keys(values).some(key => !columns.some(column => column.name === key))) {
    throw new Error("Identity profile contains an unknown column for " + name);
  }
  const entries: Array<[string, string | number | null]> = [];
  for (const column of columns) {
    let value = values[column.name];
    if (value === undefined && column.generation === "application_uuid") value = randomUUID();
    if (value === undefined) {
      if (column.default !== null || column.nullable) continue;
      throw new Error("Identity provisioning requires " + name + "." + column.name + " in the supplied profile");
    }
    if (value === null) {
      if (!column.nullable) throw new Error("Identity profile cannot omit " + name + "." + column.name);
    } else if (column.type === "boolean") {
      if (typeof value !== "boolean" && value !== 0 && value !== 1) throw new Error("Invalid boolean identity profile field");
      value = value ? 1 : 0;
    } else if (column.type === "json") {
      value = JSON.stringify(value);
    } else if (column.type === "integer" || column.type === "number") {
      if (typeof value !== "number" || !Number.isFinite(value) || (column.type === "integer" && !Number.isInteger(value))) {
        throw new Error("Invalid numeric identity profile field");
      }
    } else if (typeof value !== "string") {
      throw new Error("Invalid string identity profile field");
    }
    entries.push([column.name, value as string | number | null]);
  }
  connection.prepare("INSERT INTO " + table(name) + " (" + entries.map(([key]) => quote(key)).join(", ")
    + ") VALUES (" + entries.map(() => "?").join(", ") + ")").run(...entries.map(([, value]) => value));
}

/** Seed canonical policy roles; preserve existing accounts, memberships, names and sessions. */
export function initializeIdentity(connection: Connection = sqliteDatabase): void {
  const profiles = parseObject(process.env.ARC_AUTH_ROLE_PROFILES, "ARC_AUTH_ROLE_PROFILES");
  connection.transaction(() => {
    for (const definition of config.roles) {
      const existing = connection.prepare("SELECT id FROM " + table(config.role_table) + " WHERE "
        + quote(config.role_key_column) + " = ?").get(definition.id) as Row | undefined;
      if (existing) continue;
      const names = [...new Set([definition.name, ...definition.aliases])];
      const matches = connection.prepare("SELECT id, " + quote(config.role_key_column) + " AS role_key FROM "
        + table(config.role_table) + " WHERE " + quote(config.role_name_column) + " IN ("
        + names.map(() => "?").join(",") + ")").all(...names) as Row[];
      if (matches.length > 1 || (matches[0]?.role_key && matches[0].role_key !== definition.id)) {
        throw new Error("Ambiguous existing role mapping for policy ID " + definition.id);
      }
      if (matches[0]) {
        connection.prepare("UPDATE " + table(config.role_table) + " SET " + quote(config.role_key_column)
          + " = ? WHERE id = ?").run(definition.id, matches[0].id);
        continue;
      }
      const profile = profiles[definition.id] ?? {};
      if (!profile || typeof profile !== "object" || Array.isArray(profile)) throw new Error("Invalid identity role profile");
      const values: Row = { ...(profile as Row), id: randomUUID(),
        [config.role_name_column]: definition.name, [config.role_key_column]: definition.id };
      if (config.role_administrative_column) values[config.role_administrative_column] = definition.administrative;
      // The explicitly mapped administrative attribute is descriptive; policy IDs identify permissions.
      insertRow(connection, config.role_table, config.role_columns, values);
    }
  })();
}

function subjectForUser(userId: string, connection: Connection): IdentitySubject | null {
  const row = connection.prepare("SELECT u.id, u." + quote(config.login_column) + " AS username, u."
    + quote(config.display_column) + " AS display_name, c.team_id FROM " + table(config.user_table) + " u JOIN "
    + table(config.credential_table) + " c ON c." + quote(config.user_fk)
    + " = u.id WHERE u.id = ? AND c.disabled = 0").get(userId) as Row | undefined;
  if (!row) return null;
  const memberships = connection.prepare("SELECT r." + quote(config.role_key_column) + " AS role_key FROM "
    + table(config.membership_table) + " m JOIN " + table(config.role_table) + " r ON r.id = m."
    + quote(config.membership_role_fk) + " WHERE m." + quote(config.membership_user_fk) + " = ?").all(userId) as Row[];
  const roles = [...memberships.map(item => item.role_key), ...config.role_bindings
    .filter(binding => binding.subject_ref === userId).map(binding => binding.role_ref)]
    .filter((value): value is string => typeof value === "string" && config.roles.some(role => role.id === value));
  return Object.freeze({ id: String(row.id), username: String(row.username),
    display_name: String(row.display_name ?? row.username), roles: Object.freeze([...new Set(roles)].sort()),
    ...(typeof row.team_id === "string" ? { team_id: row.team_id } : {}) });
}

export interface ProvisionIdentityAccount {
  username: string;
  password: string;
  roles: readonly string[];
  profile?: Row;
  team_id?: string | null;
}

/** Attach a credential to an existing domain user, or create a user from an explicit profile. */
export function provisionIdentityAccount(input: ProvisionIdentityAccount, connection: Connection = sqliteDatabase): IdentitySubject {
  if (typeof input.username !== "string" || !input.username.trim() || input.username.length > 256
      || !validPasswordInput(input.password)) throw new Error("Invalid identity account credentials");
  if (input.team_id !== undefined && input.team_id !== null
      && (typeof input.team_id !== "string" || !input.team_id.trim())) throw new Error("Invalid trusted team ID");
  const roles = validateRoles(input.roles);
  const encoded = hashIdentityPassword(input.password);
  return connection.transaction(() => {
    initializeIdentity(connection);
    let user = connection.prepare("SELECT id FROM " + table(config.user_table) + " WHERE "
      + quote(config.login_column) + " = ?").get(input.username) as Row | undefined;
    if (!user) {
      const id = randomUUID();
      insertRow(connection, config.user_table, config.user_columns,
        { ...input.profile, id, [config.login_column]: input.username });
      user = { id };
    }
    const id = String(user.id);
    const existing = connection.prepare("SELECT id FROM " + table(config.credential_table) + " WHERE "
      + quote(config.user_fk) + " = ?").get(id);
    if (existing) throw new Error("Identity account already has a credential; use the explicit password/role management functions");
    connection.prepare("INSERT INTO " + table(config.credential_table) + " (id, " + quote(config.user_fk)
      + ", password_hash, disabled, team_id) VALUES (?, ?, ?, 0, ?)")
      .run(randomUUID(), id, encoded, input.team_id ?? null);
    setIdentityRoles(id, roles, connection);
    return subjectForUser(id, connection)!;
  })();
}

export function setIdentityRoles(userId: string, roleIds: readonly string[], connection: Connection = sqliteDatabase): void {
  const roles = validateRoles(roleIds);
  connection.transaction(() => {
    if (!connection.prepare("SELECT id FROM " + table(config.user_table) + " WHERE id = ?").get(userId)) throw new Error("Unknown identity subject");
    connection.prepare("DELETE FROM " + table(config.membership_table) + " WHERE "
      + quote(config.membership_user_fk) + " = ?").run(userId);
    for (const key of roles) {
      const role = connection.prepare("SELECT id FROM " + table(config.role_table) + " WHERE "
        + quote(config.role_key_column) + " = ?").get(key) as Row | undefined;
      if (!role) throw new Error("Policy role has not been initialized: " + key);
      connection.prepare("INSERT INTO " + table(config.membership_table) + " (id, "
        + quote(config.membership_user_fk) + ", " + quote(config.membership_role_fk) + ") VALUES (?, ?, ?)")
        .run(randomUUID(), userId, role.id);
    }
  })();
}

export function setIdentityPassword(userId: string, password: string, connection: Connection = sqliteDatabase): void {
  const encoded = hashIdentityPassword(password);
  connection.transaction(() => {
    const result = connection.prepare("UPDATE " + table(config.credential_table)
      + " SET password_hash = ? WHERE " + quote(config.user_fk) + " = ?").run(encoded, userId);
    if (!result.changes) throw new Error("Unknown identity account");
    connection.prepare("DELETE FROM " + table(config.session_table) + " WHERE " + quote(config.user_fk) + " = ?").run(userId);
  })();
}

export function setIdentityDisabled(userId: string, disabled: boolean, connection: Connection = sqliteDatabase): void {
  if (typeof disabled !== "boolean") throw new Error("disabled must be boolean");
  connection.transaction(() => {
    const result = connection.prepare("UPDATE " + table(config.credential_table)
      + " SET disabled = ? WHERE " + quote(config.user_fk) + " = ?").run(disabled ? 1 : 0, userId);
    if (!result.changes) throw new Error("Unknown identity account");
    if (disabled) connection.prepare("DELETE FROM " + table(config.session_table) + " WHERE " + quote(config.user_fk) + " = ?").run(userId);
  })();
}

/** Explicit one-time bootstrap. Existing credentials/role assignments are never overwritten on restart. */
export function bootstrapIdentityFromEnv(connection: Connection = sqliteDatabase): void {
  const override = process.env.ARC_AUTH_BOOTSTRAP_USERNAME !== undefined || process.env.ARC_AUTH_BOOTSTRAP_PASSWORD !== undefined;
  const defaults = override ? undefined : config.bootstrap_account;
  const username = override ? process.env.ARC_AUTH_BOOTSTRAP_USERNAME : defaults?.username;
  const password = override ? process.env.ARC_AUTH_BOOTSTRAP_PASSWORD : defaults?.password;
  if (username === undefined && password === undefined) return;
  if (!username || !password) throw new Error("Set both ARC_AUTH_BOOTSTRAP_USERNAME and ARC_AUTH_BOOTSTRAP_PASSWORD");
  const existing = connection.prepare("SELECT c.id FROM " + table(config.credential_table) + " c JOIN "
    + table(config.user_table) + " u ON u.id = c." + quote(config.user_fk) + " WHERE u."
    + quote(config.login_column) + " = ?").get(username);
  if (existing) return;
  const roles = process.env.ARC_AUTH_BOOTSTRAP_ROLES === undefined ? (defaults?.roles ?? config.bootstrap_roles)
    : process.env.ARC_AUTH_BOOTSTRAP_ROLES.split(",").map(value => value.trim()).filter(Boolean);
  provisionIdentityAccount({ username, password, roles,
    profile: { ...defaults?.profile, ...parseObject(process.env.ARC_AUTH_BOOTSTRAP_PROFILE, "ARC_AUTH_BOOTSTRAP_PROFILE") } }, connection);
}

export function sessionLifetime(): number {
  const seconds = Number(process.env.ARC_AUTH_SESSION_TTL_SECONDS ?? "28800");
  if (!Number.isInteger(seconds) || seconds < 60 || seconds > 604800) throw new Error("ARC_AUTH_SESSION_TTL_SECONDS must be 60..604800");
  return seconds * 1000;
}

export async function authenticateIdentity(username: string, password: string, connection: Connection = sqliteDatabase)
  : Promise<{ token: string; session: IdentitySession } | null> {
  const row = connection.prepare("SELECT u.id, c.password_hash, c.disabled FROM " + table(config.user_table)
    + " u JOIN " + table(config.credential_table) + " c ON c." + quote(config.user_fk)
    + " = u.id WHERE u." + quote(config.login_column) + " = ?").get(username) as Row | undefined;
  const encoded = row && !row.disabled ? String(row.password_hash) : null;
  if (!await verifyIdentityPassword(password, encoded) || !row) return null;
  return connection.transaction(() => {
    // Recheck after async password derivation: a concurrent disable/reset must take effect.
    const credential = connection.prepare("SELECT password_hash, disabled FROM " + table(config.credential_table)
      + " WHERE " + quote(config.user_fk) + " = ?").get(row.id) as Row | undefined;
    if (!credential || credential.disabled || credential.password_hash !== encoded) return null;
    const subject = subjectForUser(String(row.id), connection);
    if (!subject) return null;
    const token = randomBytes(32).toString("base64url");
    const now = identityNow(), expires = now + sessionLifetime();
    connection.prepare("DELETE FROM " + table(config.session_table) + " WHERE expires_at <= ?").run(now);
    connection.prepare("INSERT INTO " + table(config.session_table) + " (id, " + quote(config.user_fk)
      + ", token_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?)")
      .run(randomUUID(), row.id, tokenHash(token), now, expires);
    return { token, session: { subject, csrf_token: csrfToken(token), expires_at: expires } };
  })();
}

export function identitySessionForToken(token: string, connection: Connection = sqliteDatabase): IdentitySession | null {
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) return null;
  const row = connection.prepare("SELECT " + quote(config.user_fk) + " AS user_id, expires_at FROM "
    + table(config.session_table) + " WHERE token_hash = ?").get(tokenHash(token)) as Row | undefined;
  if (!row) return null;
  if (Number(row.expires_at) <= identityNow()) { revokeIdentitySession(token, connection); return null; }
  const subject = subjectForUser(String(row.user_id), connection);
  if (!subject) { revokeIdentitySession(token, connection); return null; }
  return { subject, csrf_token: csrfToken(token), expires_at: Number(row.expires_at) };
}

export function revokeIdentitySession(token: string, connection: Connection = sqliteDatabase): void {
  if (/^[A-Za-z0-9_-]{43}$/.test(token)) connection.prepare("DELETE FROM " + table(config.session_table)
    + " WHERE token_hash = ?").run(tokenHash(token));
}
