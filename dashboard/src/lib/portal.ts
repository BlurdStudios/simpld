// Portal plumbing shared by the Worker entry and API handlers:
// composite push-key resolution (D1 api_keys table first, SITE_KEYS env map
// fallback) + session lookup + small response helpers.

import {
  clearSessionCookie,
  sessionTokenFrom,
  setSessionCookie,
  type AsyncKeyStore,
  type SessionUser,
} from "./auth";
import { sha256Hex } from "./password";
import type { AccountsStore, PublicUser } from "./db";

export function json(data: unknown, status = 200, headers?: HeadersInit): Response {
  return Response.json(data, { status, headers });
}

export function needAuth(): Response {
  return json({ error: "login required" }, 401);
}

export function needAdmin(): Response {
  return json({ error: "vendor_admin required" }, 403);
}

/** Push/viewer keys: D1 api_keys (unrevoked) wins; SITE_KEYS env map is fallback. */
export function compositeKeyStore(
  env: Record<string, string | undefined>,
  accounts: AccountsStore
): AsyncKeyStore {
  let mapping: Record<string, string> = {};
  try {
    if (env.SITE_KEYS) mapping = JSON.parse(env.SITE_KEYS) as Record<string, string>;
  } catch {
    mapping = {};
  }
  const reverse = new Map<string, string>();
  for (const [site, key] of Object.entries(mapping)) reverse.set(key, site);
  if (reverse.size === 0) reverse.set("dev-key", "demo-site");
  return {
    async siteForKey(apiKey: string): Promise<string | null> {
      const hash = await sha256Hex(apiKey);
      const viaDb = await accounts.siteForKeyHash(hash);
      if (viaDb) return viaDb;
      return reverse.get(apiKey) ?? null;
    },
  };
}

export async function getSessionUser(req: Request, accounts: AccountsStore): Promise<PublicUser | null> {
  const token = sessionTokenFrom(req);
  if (!token) return null;
  return accounts.sessionUser(token);
}

export function toSessionUser(u: PublicUser): SessionUser {
  return { id: u.id, email: u.email, role: u.role, site_id: u.site_id };
}

export function loginResponse(user: PublicUser, token: string): Response {
  return json(
    { ok: true, user: { id: user.id, email: user.email, role: user.role, site_id: user.site_id } },
    200,
    { "Set-Cookie": setSessionCookie(token) }
  );
}

export function logoutResponse(): Response {
  return json({ ok: true }, 200, { "Set-Cookie": clearSessionCookie() });
}
