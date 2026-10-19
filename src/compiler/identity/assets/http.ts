// Compiler-owned cookie authentication. Authorization decisions belong to business route bindings.
import express from "express";
import type { Express, Request, RequestHandler } from "express";
import { timingSafeEqual } from "node:crypto";
import { authenticateIdentity, identityNow, identitySessionForToken, revokeIdentitySession, sessionLifetime } from "./store.js";
import { validPasswordInput } from "./password.js";
import type { IdentitySession, IdentitySubject } from "./types.js";

declare global {
  namespace Express {
    interface Request { readonly identity?: IdentitySubject; }
  }
}

const sessions = new WeakMap<Request, IdentitySession>();
const failedLogins = new Map<string, { count: number; expires: number }>();

function secureCookie(): boolean {
  const configured = process.env.ARC_AUTH_COOKIE_SECURE;
  if (configured !== undefined && configured !== "0" && configured !== "1") throw new Error("ARC_AUTH_COOKIE_SECURE must be 0 or 1");
  return configured === undefined ? process.env.NODE_ENV === "production" : configured === "1";
}

function cookieName(): string { return secureCookie() ? "__Host-arc_session" : "arc_session"; }
const cookieOptions = () => ({ httpOnly: true, secure: secureCookie(), sameSite: "lax" as const, path: "/" });

function requestToken(request: Request): string | null {
  const values = (request.headers.cookie ?? "").split(";").map(value => value.trim())
    .filter(value => value.startsWith(cookieName() + "="));
  if (values.length !== 1) return null;
  const token = values[0].slice(cookieName().length + 1);
  return /^[A-Za-z0-9_-]{43}$/.test(token) ? token : null;
}

function sameOrigin(request: Request): boolean {
  if (request.get("Sec-Fetch-Site") === "cross-site") return false;
  const origin = request.get("Origin");
  if (!origin) return true; // Non-browser HTTP clients still require a session and CSRF token.
  try {
    const url = new URL(origin);
    return (url.protocol === "http:" || url.protocol === "https:") && url.host === request.get("Host");
  } catch { return false; }
}

export const identityContext: RequestHandler = (request, response, next) => {
  try {
    const token = requestToken(request);
    if (token) {
      response.setHeader("Cache-Control", "no-store");
      const session = identitySessionForToken(token);
      if (session) {
        sessions.set(request, session);
        Object.defineProperty(request, "identity", { value: session.subject, writable: false, enumerable: true });
      } else response.clearCookie(cookieName(), cookieOptions());
    }
    next();
  } catch { response.status(503).json({ error: "identity_unavailable" }); }
};

export const requireCsrf: RequestHandler = (request, response, next) => {
  const session = sessions.get(request);
  if (!session) { response.status(401).json({ error: "authentication_required" }); return; }
  const token = request.get("X-CSRF-Token") ?? "";
  if (!sameOrigin(request) || !/^[a-f0-9]{64}$/.test(token)
      || !timingSafeEqual(Buffer.from(token, "hex"), Buffer.from(session.csrf_token, "hex"))) {
    response.status(403).json({ error: "invalid_csrf_token" }); return;
  }
  next();
};

/** Use before business handlers that require a subject; unsafe requests also require CSRF. */
export const requireIdentity: RequestHandler = (request, response, next) => {
  response.setHeader("Cache-Control", "no-store");
  if (!sessions.has(request)) { response.status(401).json({ error: "authentication_required" }); return; }
  if (!["GET", "HEAD", "OPTIONS"].includes(request.method)) { requireCsrf(request, response, next); return; }
  next();
};

/** Mount exactly once before business routes, including anonymous pages. */
export function installIdentity(app: Express): void {
  secureCookie(); sessionLifetime(); // Reject malformed security configuration at application creation.
  app.use(identityContext);
  const router = express.Router();
  router.use((_request, response, next) => { response.setHeader("Cache-Control", "no-store"); next(); });
  router.post("/login", async (request, response) => {
    if (!sameOrigin(request)) { response.status(403).json({ error: "invalid_origin" }); return; }
    const body: unknown = request.body;
    if (!body || typeof body !== "object" || Array.isArray(body)
        || Object.keys(body).some(key => !["username", "password"].includes(key))) {
      response.status(400).json({ error: "invalid_login_request" }); return;
    }
    const { username, password } = body as { username?: unknown; password?: unknown };
    if (typeof username !== "string" || !username.trim() || username.length > 256 || !validPasswordInput(password)) {
      response.status(400).json({ error: "invalid_login_request" }); return;
    }
    const key = request.ip ?? request.socket.remoteAddress ?? "unknown";
    const now = identityNow();
    const previous = failedLogins.get(key);
    const failures = previous && previous.expires > now ? previous : { count: 0, expires: now + 60000 };
    if (failures.count >= 10) {
      response.setHeader("Retry-After", Math.max(1, Math.ceil((failures.expires - now) / 1000)));
      response.status(429).json({ error: "login_rate_limited" }); return;
    }
    failures.count++;
    failedLogins.set(key, failures);
    if (failedLogins.size > 4096) failedLogins.delete(failedLogins.keys().next().value!);
    try {
      const result = await authenticateIdentity(username, password);
      if (!result) { response.status(401).json({ error: "invalid_credentials" }); return; }
      failedLogins.delete(key);
      const old = requestToken(request);
      if (old) revokeIdentitySession(old);
      response.cookie(cookieName(), result.token, { ...cookieOptions(), maxAge: sessionLifetime() });
      response.json(result.session);
    } catch { response.status(503).json({ error: "identity_unavailable" }); }
  });
  router.get("/me", requireIdentity, (request, response) => response.json(sessions.get(request)));
  router.post("/logout", requireIdentity, (request, response) => {
    const token = requestToken(request);
    if (token) revokeIdentitySession(token);
    response.clearCookie(cookieName(), cookieOptions());
    response.status(204).end();
  });
  router.use((_request, response) => { response.status(404).json({ error: "identity_endpoint_not_found" }); });
  app.use("/api/auth", router);
}
