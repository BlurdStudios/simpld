// POST /api/auth/login  { site_id, password } (clients) | { email, password } (admins)
// POST /api/auth/setup  { site_id, serial, code, password } (first login, one-time)
// POST /api/auth/logout -> clears session + cookie.
// GET  /api/auth/me     -> current session user (401 when logged out).

import type { AccountsStore } from "../../lib/db";
import { toPublic } from "../../lib/db";
import { verifyPassword } from "../../lib/password";
import { hashSetupCode } from "../../lib/site-id";
import { sessionTokenFrom } from "../../lib/auth";
import { getSessionUser, json, loginResponse, logoutResponse, toSessionUser } from "../../lib/portal";

/** Generic login failure: never reveals which identifier exists. */
function invalidLogin(): Response {
  return json({ error: "invalid details" }, 401);
}

export async function handleLogin(req: Request, accounts: AccountsStore): Promise<Response> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return json({ error: "invalid json" }, 400);
  }
  const b = body as Record<string, unknown>;
  const email = typeof b.email === "string" ? b.email : "";
  const siteId = typeof b.site_id === "string" ? b.site_id : "";
  const password = typeof b.password === "string" ? b.password : "";
  if (!password || (!email && !siteId)) return json({ error: "site_id or email and password required" }, 400);
  const user = siteId
    ? await accounts.findUserBySiteId(siteId)
    : await accounts.findUserByEmail(email);
  if (!user || user.disabled === 1) return invalidLogin();
  if (user.role === "client" && user.setup_done === 0) {
    return json({ error: "finish setup first" }, 409);
  }
  if (!(await verifyPassword(password, user.pass_hash))) return invalidLogin();
  const { token } = await accounts.createSession(user.id);
  return loginResponse(toPublic(user), token);
}

// POST /api/auth/setup — first login for a licence-created client.
// Every failure returns the SAME generic 401 so no field is oracle-able.
// TODO: rate-limit setup attempts (no rate-limit helper in the codebase yet).
export async function handleSetup(req: Request, accounts: AccountsStore): Promise<Response> {
  const generic = () => json({ error: "invalid details" }, 401);
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return generic();
  }
  const b = body as Record<string, unknown>;
  const siteId = typeof b.site_id === "string" ? b.site_id.trim() : "";
  const serial = typeof b.serial === "string" ? b.serial.trim() : "";
  const code = typeof b.code === "string" ? b.code.trim() : "";
  const password = typeof b.password === "string" ? b.password : "";
  if (!siteId || !serial || !code || !password) return generic();
  const user = await accounts.findUserBySiteId(siteId);
  if (!user || user.role !== "client" || user.disabled === 1) return generic();
  if (user.setup_done !== 0 || !user.setup_code_hash || !user.setup_expires) return generic();
  if (user.setup_expires <= new Date().toISOString()) return generic();
  const want = await hashSetupCode(code, user.site_id ?? siteId);
  if (want !== user.setup_code_hash) return generic();
  // The serial must match a licence previously issued for this site.
  const rows = await accounts.listLicences(user.site_id ?? siteId);
  if (!rows.some((r) => r.serial === serial)) return generic();
  let updated;
  try {
    updated = await accounts.completeSetup(user.id, password); // throws when < 8 chars
  } catch {
    return generic();
  }
  const { token } = await accounts.createSession(user.id);
  return loginResponse(updated, token);
}

export async function handleLogout(req: Request, accounts: AccountsStore): Promise<Response> {
  const token = sessionTokenFrom(req);
  if (token) await accounts.deleteSession(token);
  return logoutResponse();
}

export async function handleMe(req: Request, accounts: AccountsStore): Promise<Response> {
  const user = await getSessionUser(req, accounts);
  if (!user) return json({ error: "login required" }, 401);
  return json({ ok: true, user: toSessionUser(user) });
}
