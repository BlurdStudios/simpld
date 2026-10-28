// Admin sites overview (vendor_admin only).
//   GET  /api/admin/sites                 all known sites (push registry +
//        reporter fingerprints + active key counts + frozen flag)
//   GET  /api/admin/sites/:siteId/reporters   reporter history for one site
//   POST /api/admin/sites/:siteId/freeze      revoke ALL keys for the site
//        (pushes + viewer go 401; cached data stays frozen) — the "revoke keys"
//        side of the freeze viewer.
//   POST /api/admin/sites/:siteId/unfreeze    clear the frozen flag (new keys
//        still need to be issued before pushes resume).

import type { AccountsStore } from "../../lib/db";
import { json } from "../../lib/portal";

export async function handleAdminSites(
  req: Request,
  accounts: AccountsStore,
  subpath: string
): Promise<Response> {
  if (req.method === "GET" && !subpath) {
    return json({ ok: true, sites: await accounts.sitesOverview() });
  }
  const rep = /^\/([^/]+)\/reporters$/.exec(subpath);
  if (req.method === "GET" && rep) {
    const siteId = decodeURIComponent(rep[1]);
    return json({
      ok: true,
      site_id: siteId,
      reporters: await accounts.reporterHistory(siteId),
    });
  }
  const fr = /^\/([^/]+)\/freeze$/.exec(subpath);
  if (req.method === "POST" && fr) {
    const siteId = decodeURIComponent(fr[1]);
    const revoked = await accounts.revokeSiteKeys(siteId);
    await accounts.setSiteFrozen(siteId, true);
    return json({ ok: true, site_id: siteId, revoked_keys: revoked, frozen: true });
  }
  const un = /^\/([^/]+)\/unfreeze$/.exec(subpath);
  if (req.method === "POST" && un) {
    const siteId = decodeURIComponent(un[1]);
    await accounts.setSiteFrozen(siteId, false);
    return json({ ok: true, site_id: siteId, frozen: false });
  }
  return json({ error: "not found" }, 404);
}
