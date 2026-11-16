// Licence heartbeat + trial requests (factory side, key-bound to site_id).
//
// POST /api/licence-heartbeat  { site_id, licence?, reporter?, ack_serial? }
//   Records the licence snapshot, acks a previously delivered offer, and
//   returns the next pending offer (one-time delivery) or null.
// POST /api/trial-request      { site_id, days, reason? }
//   Factory asks the vendor to extend a trial. Vendor decides on the dashboard.
// GET  /api/trial-requests     (vendor_admin session) pending + decided requests
// POST /api/trial-requests/decide (vendor_admin) { id, approve, days? }
//   Approve issues a fresh trial licence for the site as a new offer.

import { authorizeAsync, type AsyncKeyStore } from "../../lib/auth";
import { extractSiteLicence } from "../../lib/contract";
import type { AccountsStore, PublicUser } from "../../lib/db";
import { issueLicence } from "../../lib/license";
import { OfferStore } from "../../lib/offers";
import { json } from "../../lib/portal";

const MAX_TRIAL_REQUEST_DAYS = 90;

export async function handleHeartbeat(
  req: Request,
  deps: { keys: AsyncKeyStore; accounts: AccountsStore; offers: OfferStore | null }
): Promise<Response> {
  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return json({ error: "invalid json" }, 400);
  }
  const siteId = typeof body.site_id === "string" ? body.site_id : "";
  if (!siteId) return json({ error: "site_id required" }, 400);
  const auth = await authorizeAsync(req, siteId, deps.keys);
  if (!auth.ok) return json({ error: auth.error }, auth.status);
  const at = new Date().toISOString();
  const lic = extractSiteLicence(body);
  if (lic) {
    try {
      await deps.accounts.upsertSiteLicence({ site_id: auth.site_id, ...lic, updated_at: at });
    } catch {
      // Tracking must never fail a heartbeat.
    }
  }
  await deps.accounts.touchSite(auth.site_id, at);
  if (!deps.offers) return json({ ok: true, offer: null });
  const ack = typeof body.ack_serial === "string" ? body.ack_serial.trim() : "";
  if (ack) await deps.offers.ackOffer(auth.site_id, ack, at);
  const next = await deps.offers.pendingOffer(auth.site_id);
  if (!next) return json({ ok: true, offer: null });
  return json({
    ok: true,
    offer: { serial: next.serial, licence: JSON.parse(next.licence_json) },
  });
}

export async function handleTrialRequest(
  req: Request,
  deps: { keys: AsyncKeyStore; offers: OfferStore | null }
): Promise<Response> {
  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return json({ error: "invalid json" }, 400);
  }
  const siteId = typeof body.site_id === "string" ? body.site_id : "";
  if (!siteId) return json({ error: "site_id required" }, 400);
  const auth = await authorizeAsync(req, siteId, deps.keys);
  if (!auth.ok) return json({ error: auth.error }, auth.status);
  const days = body.days;
  if (typeof days !== "number" || !Number.isInteger(days) || days < 1 || days > MAX_TRIAL_REQUEST_DAYS) {
    return json({ error: `days must be 1..${MAX_TRIAL_REQUEST_DAYS}` }, 400);
  }
  if (!deps.offers) return json({ error: "trial requests unavailable on this server" }, 501);
  const reason = typeof body.reason === "string" ? body.reason.slice(0, 500) : "";
  const id = `trq_${crypto.randomUUID()}`;
  await deps.offers.addTrialRequest({
    id, site_id: auth.site_id, days, reason, requested_at: new Date().toISOString(),
  });
  return json({ ok: true, id, status: "pending" }, 201);
}

export async function handleListTrialRequests(
  offers: OfferStore | null,
  caller: PublicUser
): Promise<Response> {
  if (caller.role !== "vendor_admin") return json({ error: "vendor admin only" }, 403);
  if (!offers) return json({ ok: true, requests: [] });
  return json({ ok: true, requests: await offers.listTrialRequests() });
}

export async function handleDecideTrialRequest(
  req: Request,
  env: Record<string, string | undefined>,
  deps: { accounts: AccountsStore; offers: OfferStore | null },
  caller: PublicUser
): Promise<Response> {
  if (caller.role !== "vendor_admin") return json({ error: "vendor admin only" }, 403);
  if (!deps.offers) return json({ error: "trial requests unavailable on this server" }, 501);
  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return json({ error: "invalid json" }, 400);
  }
  const id = typeof body.id === "string" ? body.id : "";
  const row = id ? await deps.offers.getTrialRequest(id) : null;
  if (!row) return json({ error: "unknown request" }, 404);
  if (row.status !== "pending") return json({ error: `already ${row.status}` }, 409);
  const at = new Date().toISOString();
  if (body.approve !== true) {
    await deps.offers.decideTrialRequest(id, "denied", caller.email, at);
    return json({ ok: true, status: "denied" });
  }
  const priv = (env.LICENSE_PRIVKEY_B64 ?? "").trim();
  if (!priv) return json({ error: "licence signing not configured (LICENSE_PRIVKEY_B64)" }, 501);
  const rows = await deps.accounts.listLicences(row.site_id);
  const prev = rows[0];
  if (!prev) return json({ error: "site has no licence to extend" }, 400);
  if (prev.edition !== "trial") {
    return json({ error: "trial extensions apply to trial licences only; issue a plan instead" }, 400);
  }
  const days = typeof body.days === "number" && Number.isInteger(body.days) ? body.days : row.days;
  if (days < 1 || days > MAX_TRIAL_REQUEST_DAYS) {
    return json({ error: `days must be 1..${MAX_TRIAL_REQUEST_DAYS}` }, 400);
  }
  try {
    const lic = await issueLicence(
      {
        customer: prev.customer || prev.site_id,
        site_id: prev.site_id,
        edition: "trial",
        packager_fp: prev.packager_fp,
        trial_days: days,
      },
      priv
    );
    await deps.accounts.logLicence({
      site_id: lic.site_id, customer: lic.customer, serial: lic.serial, edition: lic.edition,
      packager_fp: lic.packager_fp, max_stations: lic.max_stations, issued: lic.issued,
      expires: lic.expires, max_version: lic.max_version,
      issued_by: `${caller.email} (trial request ${id}, ${days}d)`,
    });
    await deps.offers.addOffer({
      id: `ofr_${crypto.randomUUID()}`, site_id: lic.site_id, serial: lic.serial,
      packager_fp: lic.packager_fp, licence_json: JSON.stringify(lic), created_at: at,
    });
    await deps.offers.decideTrialRequest(id, "approved", caller.email, at);
    return json({ ok: true, status: "approved", serial: lic.serial, expires: lic.expires }, 201);
  } catch (e) {
    return json({ error: e instanceof Error ? e.message : "approval failed" }, 400);
  }
}
