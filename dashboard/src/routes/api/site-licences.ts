// GET /api/admin/site-licences (vendor_admin only)
// Live licence snapshots pushed by factories (edition/expiry/countdown/
// state/app version per site) for upgrade + trial-nudge tracking.
// Sites with issuance-log rows but no snapshot never activated a key.
import type { AccountsStore } from "../../lib/db";
import { json } from "../../lib/portal";

export async function handleListSiteLicences(accounts: AccountsStore) {
  const [tracked, issued] = await Promise.all([
    accounts.listSiteLicences(),
    accounts.listLicences(),
  ]);
  const latestIssued = new Map<string, { issued: string; issued_by: string; edition: string }>();
  for (const l of issued) {
    if (!latestIssued.has(l.site_id)) {
      latestIssued.set(l.site_id, { issued: l.issued, issued_by: l.issued_by, edition: l.edition });
    }
  }
  return json({ ok: true, tracked, issued: [...latestIssued].map(([site_id, v]) => ({ site_id, ...v })) });
}
