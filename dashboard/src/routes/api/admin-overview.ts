// GET /api/admin/overview  (vendor_admin only)
// One payload for the Admin overview tab: fleet counts + attention lists.
import type { AccountsStore } from "../../lib/db";
import { json } from "../../lib/portal";

const DAY = 86400000;

export async function handleAdminOverview(accounts: AccountsStore): Promise<Response> {
  const [sites, licences] = await Promise.all([accounts.sitesOverview(), accounts.listLicences()]);
  const now = Date.now();
  const liveCut = now - 7 * DAY;
  const expCut = now + 30 * DAY;

  // Latest licence per site decides the site's licence state.
  const latest = new Map<string, (typeof licences)[number]>();
  for (const l of licences) {
    const cur = latest.get(l.site_id);
    if (!cur || l.created_at > cur.created_at) latest.set(l.site_id, l);
  }
  let expiring = 0;
  const expiringRows: Array<{ site_id: string; serial: string; expires: string; days_left: number }> = [];
  for (const l of latest.values()) {
    const left = Math.round((Date.parse(l.expires) - now) / DAY);
    if (left <= 30) {
      expiring++;
      expiringRows.push({ site_id: l.site_id, serial: l.serial, expires: l.expires, days_left: left });
    }
  }
  expiringRows.sort((a, b) => a.days_left - b.days_left);

  const live = sites.filter((s) => s.last_push_at && Date.parse(s.last_push_at) >= liveCut).length;
  const frozen = sites.filter((s) => s.frozen).length;
  const neverPushed = sites.filter((s) => !s.last_push_at).map((s) => s.site_id);
  const recent = licences.slice(0, 5);

  return json({
    ok: true,
    overview: {
      sites_total: sites.length,
      sites_live_7d: live,
      sites_never_pushed: neverPushed,
      licences_total: licences.length,
      licences_expiring_30d: expiring,
      expiring: expiringRows.slice(0, 10),
      frozen_sites: frozen,
      recent,
    },
  });
}
