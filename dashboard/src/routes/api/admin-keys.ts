// Admin API-key management (vendor_admin only).
//   GET  /api/admin/keys[?user_id=]  list (hashes never leave the server)
//   POST /api/admin/keys             { user_id, site_id, label?, confirm } create;
//        creating a key revokes the user's other active keys (one active key
//        per user) — requires explicit { confirm: true }.
//   POST /api/admin/keys/:id/revoke  revoke one key (pushes + viewer 401 after).

import type { AccountsStore } from "../../lib/db";
import { json } from "../../lib/portal";

export async function handleAdminKeys(
  req: Request,
  accounts: AccountsStore,
  subpath: string,
  query: URLSearchParams
): Promise<Response> {
  if (req.method === "GET" && !subpath) {
    const userId = query.get("user_id") || undefined;
    return json({ ok: true, keys: await accounts.listApiKeys(userId) });
  }
  if (req.method === "POST" && !subpath) {
    let body: unknown;
    try {
      body = await req.json();
    } catch {
      return json({ error: "invalid json" }, 400);
    }
    const b = body as Record<string, unknown>;
    const userId = typeof b.user_id === "string" ? b.user_id : "";
    const siteId = typeof b.site_id === "string" ? b.site_id : "";
    const label = typeof b.label === "string" ? b.label : "";
    if (!userId || !siteId) return json({ error: "user_id and site_id required" }, 400);
    if (b.confirm !== true) {
      return json(
        { error: "creating a key revokes the user's other active keys — resend with { confirm: true }" },
        400
      );
    }
    try {
      const { row, plaintext, revokedCount } = await accounts.createApiKey(userId, siteId, label);
      // Plaintext is shown ONCE here and never stored — the admin must copy it now.
      return json(
        {
          ok: true,
          key: plaintext,
          id: row.id,
          user_id: row.user_id,
          site_id: row.site_id,
          label: row.label,
          created_at: row.created_at,
          revoked_previous: revokedCount,
        },
        201
      );
    } catch (e) {
      return json({ error: e instanceof Error ? e.message : "create failed" }, 400);
    }
  }
  const m = /^\/([^/]+)\/revoke$/.exec(subpath);
  if (req.method === "POST" && m) {
    const ok = await accounts.revokeApiKey(m[1]);
    if (!ok) return json({ error: "unknown or already-revoked key" }, 404);
    return json({ ok: true, revoked: m[1] });
  }
  return json({ error: "not found" }, 404);
}
