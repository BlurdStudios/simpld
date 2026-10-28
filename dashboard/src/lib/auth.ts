// Auth: site_id + API key. Two accepted headers:
//   Authorization: Bearer <API_KEY>
//   X-Site-Key: <API_KEY>
// Buyer isolation: a key is bound to exactly one site_id. Fetching or pushing
// for any other site_id returns 403. Revoke = delete/disable the key mapping
// (viewer freezes: no new pushes; old cached data stays until retention prune).

export interface KeyStore {
  /** Resolve the site_id a key belongs to, or null if unknown/revoked. */
  siteForKey(apiKey: string): string | null;
}

function bearerOrHeader(req: Request): string | null {
  const auth = req.headers.get("authorization");
  if (auth) {
    const m = /^Bearer\s+(.+)$/i.exec(auth.trim());
    if (m) return m[1].trim();
  }
  const h = req.headers.get("x-site-key");
  if (h && h.trim()) return h.trim();
  return null;
}

/** Extract key, resolve site, enforce that it matches expectedSite. */
export function authorize(
  req: Request,
  expectedSite: string,
  keys: KeyStore
): { ok: true; site_id: string } | { ok: false; status: 401 | 403; error: string } {
  const key = bearerOrHeader(req);
  if (!key) return { ok: false, status: 401, error: "missing api key" };
  const bound = keys.siteForKey(key);
  if (!bound) return { ok: false, status: 401, error: "invalid or revoked api key" };
  if (bound !== expectedSite)
    return { ok: false, status: 403, error: "key is not authorized for this site" };
  return { ok: true, site_id: bound };
}

/** Async key resolver: D1 api_keys table (preferred) with sync-store fallback. */
export interface AsyncKeyStore {
  siteForKey(apiKey: string): Promise<string | null> | string | null;
}

export async function authorizeAsync(
  req: Request,
  expectedSite: string,
  keys: AsyncKeyStore
): Promise<{ ok: true; site_id: string } | { ok: false; status: 401 | 403; error: string }> {
  const key = bearerOrHeader(req);
  if (!key) return { ok: false, status: 401, error: "missing api key" };
  const bound = await keys.siteForKey(key);
  if (!bound) return { ok: false, status: 401, error: "invalid or revoked api key" };
  if (bound !== expectedSite)
    return { ok: false, status: 403, error: "key is not authorized for this site" };
  return { ok: true, site_id: bound };
}

// ---------- portal sessions (vendor-provisioned accounts, SYNC_CONTRACT v2) ----------

export const SESSION_COOKIE = "simpld_session";

export function sessionTokenFrom(req: Request): string | null {
  const raw = req.headers.get("cookie");
  if (!raw) return null;
  for (const part of raw.split(";")) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    if (part.slice(0, eq).trim() === SESSION_COOKIE) {
      const v = part.slice(eq + 1).trim();
      if (/^[0-9a-f]{16,128}$/.test(v)) return v;
    }
  }
  return null;
}

export function setSessionCookie(token: string, maxAgeSecs = 7 * 86400): string {
  // No Secure flag: the portal may run over plain HTTP on a LAN. SameSite=Lax
  // + HttpOnly + random 256-bit token is the protection; production HTTPS
  // deployments should terminate TLS at Cloudflare (default) anyway.
  return `${SESSION_COOKIE}=${token}; HttpOnly; Path=/; SameSite=Lax; Max-Age=${maxAgeSecs}`;
}

export function clearSessionCookie(): string {
  return `${SESSION_COOKIE}=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0`;
}

export interface SessionUser {
  id: string;
  email: string;
  role: "vendor_admin" | "client";
  site_id: string | null;
}

/** Client users may only touch their own site; vendor_admin may touch any. */
export function canAccessSite(user: SessionUser, siteId: string): boolean {
  if (user.role === "vendor_admin") return true;
  return !!user.site_id && user.site_id === siteId;
}

/** Env-backed key store. In production set per-site keys as Worker secrets and
 *  map them here, or back siteForKey with KV/D1. Format of SITE_KEYS secret:
 *  JSON object {"<site_id>": "<api_key>", ...}. */
export function envKeyStore(env: Record<string, string | undefined>): KeyStore {
  let mapping: Record<string, string> = {};
  try {
    if (env.SITE_KEYS) mapping = JSON.parse(env.SITE_KEYS) as Record<string, string>;
  } catch {
    mapping = {};
  }
  const reverse = new Map<string, string>();
  for (const [site, key] of Object.entries(mapping)) reverse.set(key, site);
  // Dev fallback so `wrangler dev` works out of the box (NOT for production).
  if (reverse.size === 0) reverse.set("dev-key", "demo-site");
  return { siteForKey: (k) => reverse.get(k) ?? null };
}
