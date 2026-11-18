// Admin user management (vendor_admin only).
//   GET  /api/admin/users                 list (password hashes never leave the server)
//   POST /api/admin/users                 { email, password, role, site_id? } create
//   POST /api/admin/users/:id             { role?, site_id?, disabled? } update / disable-enable
//   POST /api/admin/users/:id/password    { password } set/replace a password
//
// Deliberately simple (layman-simple portal model): the vendor sets a
// password and shares it with the user out-of-band. No one-time codes.

import type { AccountsStore, PublicUser, Role } from "../../lib/db";
import { toPublic } from "../../lib/db";
import { json } from "../../lib/portal";
import { bloomMaybeTaken, ensureBloomSeeded, noteSiteId, suggestSiteIds } from "../../lib/site-id";

/** DB-confirmed availability, with the bloom filter as a fast pre-check only. */
async function siteFree(accounts: AccountsStore, id: string): Promise<boolean> {
  await ensureBloomSeeded(accounts, async () => (await accounts.listUsers()).map((u) => u.site_id ?? ""));
  if (!bloomMaybeTaken(id)) return true; // bloom negative: not in this store
  return !(await accounts.siteIdInUse(id)); // "maybe taken" -> the DB decides
}

function sanitize(u: PublicUser): PublicUser {
  return u;
}

export async function handleAdminUsers(
  req: Request,
  accounts: AccountsStore,
  subpath: string
): Promise<Response> {
  if (req.method === "GET" && !subpath) {
    return json({ ok: true, users: await accounts.listUsersAdmin() });
  }
  if (req.method === "POST" && !subpath) {
    let body: unknown;
    try {
      body = await req.json();
    } catch {
      return json({ error: "invalid json" }, 400);
    }
    const b = body as Record<string, unknown>;
    const email = typeof b.email === "string" ? b.email : "";
    const password = typeof b.password === "string" ? b.password : "";
    const role = typeof b.role === "string" ? (b.role as Role) : "client";
    const siteId = typeof b.site_id === "string" && b.site_id.trim() ? b.site_id.trim() : null;
    if (!email || !password) return json({ error: "email and password required" }, 400);
    if (role === "client" && siteId && !(await siteFree(accounts, siteId))) {
      const suggestions = await suggestSiteIds(siteId, 3, async (id) => !(await siteFree(accounts, id)));
      return json({ error: "site_id taken", suggestions }, 409);
    }
    try {
      const user = await accounts.createUser(email, password, role, siteId);
      if (user.site_id) noteSiteId(user.site_id);
      return json({ ok: true, user: sanitize(user) }, 201);
    } catch (e) {
      return json({ error: e instanceof Error ? e.message : "create failed" }, 400);
    }
  }
  const parts = subpath.replace(/^\//, "").split("/");
  const id = parts[0];
  // POST /api/admin/users/:id/password — set/replace a user's password.
  // The admin types (or accepts a generated) password and shares it with the
  // user out-of-band. The password is never echoed back by the API.
  if (req.method === "POST" && id && parts[1] === "password") {
    let body: unknown;
    try {
      body = await req.json();
    } catch {
      return json({ error: "invalid json" }, 400);
    }
    const password = typeof (body as Record<string, unknown>).password === "string"
      ? ((body as Record<string, unknown>).password as string)
      : "";
    if (password.length < 8) return json({ error: "password must be at least 8 characters" }, 400);
    try {
      const user = await accounts.setPassword(id, password);
      return json({ ok: true, user: sanitize(user) });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "could not set password";
      return json({ error: msg }, msg === "unknown user" ? 404 : 400);
    }
  }
  if (req.method === "POST" && id) {
    let body: unknown;
    try {
      body = await req.json();
    } catch {
      return json({ error: "invalid json" }, 400);
    }
    const b = body as Record<string, unknown>;
    const patch: { role?: Role; site_id?: string | null; disabled?: boolean } = {};
    if (b.role !== undefined) {
      if (b.role !== "vendor_admin" && b.role !== "client") return json({ error: "invalid role" }, 400);
      patch.role = b.role;
    }
    if (b.site_id !== undefined) {
      if (b.site_id !== null && typeof b.site_id !== "string") return json({ error: "invalid site_id" }, 400);
      patch.site_id = typeof b.site_id === "string" && b.site_id.trim() ? b.site_id.trim() : null;
    }
    if (b.disabled !== undefined) {
      if (typeof b.disabled !== "boolean") return json({ error: "invalid disabled flag" }, 400);
      patch.disabled = b.disabled;
    }
    try {
      const user = await accounts.updateUser(id, patch);
      if (!user) return json({ error: "unknown user" }, 404);
      return json({ ok: true, user: sanitize(user) });
    } catch (e) {
      return json({ error: e instanceof Error ? e.message : "update failed" }, 400);
    }
  }
  return json({ error: "not found" }, 404);
}
