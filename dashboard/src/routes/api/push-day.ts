// POST /api/push-day  { site_id, day_report, reporter?, stations?, licence? }
// Shared handler used by the Worker entry (and documented for factory/sync.py).
// Auth: Authorization: Bearer <API_KEY> or X-Site-Key header, bound to site_id.
// v2: optional `reporter` {station_id, packager_fp} and `stations[]` are
// persisted for the admin Sites view; unknown fields are ignored (old factory
// builds keep passing).
// v3: optional `licence` {edition, expires, days_left, state, app_version,
// packager_fp} upserts the per-site licence snapshot for upgrade tracking.

import { authorizeAsync, type AsyncKeyStore } from "../../lib/auth";
import { extractReporter, extractSiteLicence, extractStations, validateDayReport } from "../../lib/contract";
import type { AccountsStore } from "../../lib/db";
import type { PushStore, ReporterLog } from "../../lib/store";

export async function handlePushDay(
  req: Request,
  deps: { keys: AsyncKeyStore; store: PushStore; reporters: ReporterLog; accounts: AccountsStore }
): Promise<Response> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "invalid json" }, { status: 400 });
  }
  const b = body as Record<string, unknown>;
  if (typeof b.site_id !== "string" || !b.site_id) {
    return Response.json({ error: "site_id required" }, { status: 400 });
  }
  const auth = await authorizeAsync(req, b.site_id, deps.keys);
  if (!auth.ok) return Response.json({ error: auth.error }, { status: auth.status });
  if (!validateDayReport(b.day_report)) {
    return Response.json({ error: "invalid day_report shape (see SYNC_CONTRACT.md v2)" }, { status: 400 });
  }
  const at = new Date().toISOString();
  await deps.store.saveDay(auth.site_id, b.day_report);
  await deps.reporters.touchPush(auth.site_id, at);
  const rep = extractReporter(body);
  if (rep) await deps.reporters.record(auth.site_id, { ...rep, seen_at: at });
  for (const s of extractStations(body)) {
    await deps.reporters.record(auth.site_id, { ...s, seen_at: at });
  }
  const lic = extractSiteLicence(body);
  if (lic) {
    try {
      await deps.accounts.upsertSiteLicence({ site_id: auth.site_id, ...lic, updated_at: at });
    } catch {
      // Tracking must never fail a production push.
    }
  }
  return Response.json({ ok: true, site_id: auth.site_id, day: b.day_report.day });
}
