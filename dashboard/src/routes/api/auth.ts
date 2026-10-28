// POST /api/auth/login  { email, password } -> session cookie + public user.
// POST /api/auth/logout -> clears session + cookie.
// GET  /api/auth/me     -> current session user (401 when logged out).

import type { AccountsStore } from "../../lib/db";
import { sessionTokenFrom } from "../../lib/auth";
import { getSessionUser, json, loginResponse, logoutResponse, toSessionUser } from "../../lib/portal";

export async function handleLogin(req: Request, accounts: AccountsStore): Promise<Response> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return json({ error: "invalid json" }, 400);
  }
  const b = body as Record<string, unknown>;
  const email = typeof b.email === "string" ? b.email : "";
  const password = typeof b.password === "string" ? b.password : "";
  if (!email || !password) return json({ error: "email and password required" }, 400);
  const user = await accounts.verifyLogin(email, password);
  if (!user) return json({ error: "invalid email or password" }, 401);
  const { token } = await accounts.createSession(user.id);
  return loginResponse(user, token);
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
