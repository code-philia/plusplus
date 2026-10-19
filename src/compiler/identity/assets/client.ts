// Compiler-owned browser identity client. Session secrets stay in HttpOnly cookies.
export interface IdentitySubject {
  readonly id: string;
  readonly username: string;
  readonly display_name: string;
  readonly roles: readonly string[];
  readonly team_id?: string;
}
export interface IdentitySession { subject: IdentitySubject; csrf_token: string; expires_at: number; }
let current: IdentitySession | null = null;

export class IdentityRequestError extends Error {
  constructor(public readonly status: number, public readonly code: string) { super(code); }
}

async function result(response: Response): Promise<IdentitySession> {
  const body = await response.json();
  if (!response.ok) throw new IdentityRequestError(response.status, body.error ?? "identity_request_failed");
  current = body as IdentitySession;
  return current;
}

export async function currentIdentity(): Promise<IdentitySession | null> {
  const response = await fetch("/api/auth/me", { credentials: "same-origin", cache: "no-store" });
  if (response.status === 401) { current = null; return null; }
  return result(response);
}

export async function login(username: string, password: string): Promise<IdentitySession> {
  return result(await fetch("/api/auth/login", { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }), cache: "no-store" }));
}

/** Cookie-authenticated business requests. Consumers must still handle 401/403 and apply domain authorization. */
export async function authenticatedFetch(input: string | URL, init: RequestInit = {}): Promise<Response> {
  const url = new URL(input, window.location.origin);
  if (url.origin !== window.location.origin) throw new Error("Identity requests must use the application origin");
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const session = current ?? await currentIdentity();
    if (!session) throw new IdentityRequestError(401, "authentication_required");
    headers.set("X-CSRF-Token", session.csrf_token);
  }
  const response = await fetch(url, { ...init, headers, credentials: "same-origin" });
  if (response.status === 401) current = null;
  return response;
}

export async function logout(): Promise<void> {
  if (!current && !await currentIdentity()) return;
  const response = await authenticatedFetch("/api/auth/logout", { method: "POST" });
  if (!response.ok && response.status !== 401) throw new IdentityRequestError(response.status, "logout_failed");
  current = null;
}
