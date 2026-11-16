// POST /api/licenses/issue  (session auth)
// Authenticated users sign a licence for a Machine ID (packager_fp) with the
// vendor private key (LICENSE_PRIVKEY_B64 secret, base64 raw 32B Ed25519).
// Client users may only issue for their own site_id; vendor_admin for any.
//
// The request carries ONLY site_id/customer/packager_fp/edition. Everything
// else (stations, features, issued, expiry, serial, max_version) is derived
// server-side from the edition tier rules — the UI asks for nothing manual.
// Response: the .lic JSON object. The SAME bytes are the paste-able key —
// the factory accepts the JSON pasted or imported as a file (see Licence page).
// Every issuance is logged (admin Licences view).

import type { AccountsStore, PublicUser } from "../../lib/db";
import { issueLicence } from "../../lib/license";
import { OfferStore } from "../../lib/offers";
import { hashSetupCode, newSetupCode, noteSiteId, setupExpiryIso } from "../../lib/site-id";
import type { D1Like } from "../../lib/db";

// Each issued/renewed licence becomes a one-time offer, delivered on the
// site's next heartbeat (no manual key copy). Needs the D1 database.
async function queueOffer(env: Record<string, string | undefined>, lic: { site_id: string; serial: string; packager_fp: string }, lic_full: unknown): Promise<void> {
  const db = (env as unknown as { DB?: D1Like }).DB;
  if (!db) return;
  await new OfferStore(db).addOffer({
    id: `ofr_${crypto.randomUUID()}`, site_id: lic.site_id, serial: lic.serial,
    packager_fp: lic.packager_fp, licence_json: JSON.stringify(lic_full),
    created_at: new Date().toISOString(),
  });
}
import { json } from "../../lib/portal";

export async function handleIssueLicence(
  req: Request,
  env: Record<string, string | undefined>,
  accounts: AccountsStore,
  caller: PublicUser
): Promise<Response> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return json({ error: "invalid json" }, 400);
  }
  const b = body as Record<string, unknown>;
  const siteId = typeof b.site_id === "string" ? b.site_id.trim() : "";
  if (!siteId) return json({ error: "site_id required" }, 400);
  if (caller.role !== "vendor_admin" && caller.site_id !== siteId) {
    return json({ error: "clients may only issue licences for their own site" }, 403);
  }
  const priv = (env.LICENSE_PRIVKEY_B64 ?? "").trim();
  if (!priv) {
    return json({ error: "licence signing not configured (LICENSE_PRIVKEY_B64)" }, 501);
  }
  const str = (k: string): string | undefined =>
    typeof b[k] === "string" && (b[k] as string).trim() ? (b[k] as string).trim() : undefined;
  const num = (k: string): number | undefined =>
    typeof b[k] === "number" && Number.isInteger(b[k]) ? (b[k] as number) : undefined;
  // NOTE: max_stations/features/issued/expires/serial/max_version from the
  // client are deliberately IGNORED — tier rules derive them. Forged tier
  // values (e.g. max_stations 99 on a simple licence) can never sign.
  // Exceptions: validity_days (paid presets, vendor_admin only, 1..1095d)
  // and trial_days (trial only, 1..365d) are honoured.
  const edition = str("edition") ?? "pro";
  const trialDays = edition === "trial" ? num("trial_days") : undefined;
  let validityDays: number | undefined;
  if (edition !== "trial" && caller.role === "vendor_admin") {
    const v = num("validity_days");
    if (v !== undefined) {
      if (v < 1 || v > 1095) return json({ error: "validity_days must be 1..1095" }, 400);
      validityDays = v;
    }
  }
  const input = {
    customer: str("customer") ?? "",
    site_id: siteId,
    edition,
    packager_fp: str("packager_fp") ?? "",
    ...(trialDays !== undefined ? { trial_days: trialDays } : {}),
    ...(validityDays !== undefined ? { validity_days: validityDays } : {}),
  };
  // Re-issue for a known site: no contact email needed, no setup code.
  if (await accounts.findUserBySiteId(siteId)) {
    try {
      const lic = await issueLicence(input, priv);
      await accounts.logLicence({
        site_id: lic.site_id,
        customer: lic.customer,
        serial: lic.serial,
        edition: lic.edition,
        packager_fp: lic.packager_fp,
        max_stations: lic.max_stations,
        issued: lic.issued,
        expires: lic.expires,
        max_version: lic.max_version,
        issued_by: caller.email,
      });
      await queueOffer(env, lic, lic);
      return json({ ok: true, licence: lic }, 201);
    } catch (e) {
      return json({ error: e instanceof Error ? e.message : "issuance failed" }, 400);
    }
  }
  // First issue for a site creates its client account, so a contact email is
  // REQUIRED (validated here, not just in the UI). It is checked BEFORE any
  // signing/logging, so a refusal never leaves a half-done issuance behind.
  const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  const contactRaw = str("contact_email") ?? str("email");
  if (!contactRaw) {
    return json({ error: "contact email is required for a new site — the first licence creates the client account" }, 400);
  }
  const contact = contactRaw.trim().toLowerCase();
  if (!EMAIL_RE.test(contact)) {
    return json({ error: "contact email is not a valid email address" }, 400);
  }
  if (await accounts.findUserByEmail(contact)) {
    return json({ error: "This contact email is already used by another customer. Use a different email." }, 409);
  }
  // Sign first: issueLicence is pure (no side effects), so bad input fails
  // before any row is written. Then create the account, THEN log the
  // licence — a create failure returns an explicit error with NO licence
  // issued (never a silent plain licence).
  let lic;
  try {
    lic = await issueLicence(input, priv);
  } catch (e) {
    return json({ error: e instanceof Error ? e.message : "issuance failed" }, 400);
  }
  const setupCode = newSetupCode();
  const setupExpires = setupExpiryIso(30);
  let created;
  try {
    created = await accounts.createClientWithSetup(
      lic.site_id,
      contact,
      await hashSetupCode(setupCode, lic.site_id),
      setupExpires
    );
  } catch (e) {
    // Most likely a lost create race (unique index is the authority): the
    // site appeared between our check and the insert. Tell the admin to
    // re-issue — the re-issue path attaches to the existing account.
    if (await accounts.findUserBySiteId(lic.site_id)) {
      return json({ error: "site was just onboarded by another request — re-issue the licence for this site" }, 409);
    }
    if (await accounts.findUserByEmail(contact)) {
      return json({ error: "This contact email is already used by another customer. Use a different email." }, 409);
    }
    return json({ error: e instanceof Error ? e.message : "client account could not be created — licence not issued" }, 500);
  }
  noteSiteId(lic.site_id);
  try {
    await accounts.logLicence({
      site_id: lic.site_id,
      customer: lic.customer,
      serial: lic.serial,
      edition: lic.edition,
      packager_fp: lic.packager_fp,
      max_stations: lic.max_stations,
      issued: lic.issued,
      expires: lic.expires,
      max_version: lic.max_version,
      issued_by: caller.email,
    });
    await queueOffer(env, lic, lic);
  } catch (e) {
    // Log/queue failed after the account was created: roll the account back
    // so the site is not left with a pending login but no licence to set up.
    try {
      await accounts.deleteUser(created.id);
    } catch {
      // best effort — surfacing the original failure matters more
    }
    return json({ error: e instanceof Error ? e.message : "licence could not be recorded — the client account was rolled back, try again" }, 500);
  }
  return json(
    {
      ok: true,
      licence: lic,
      client: {
        site_id: created.site_id,
        email: created.email,
        setup_code: setupCode,
        setup_expires: setupExpires,
      },
    },
    201
  );
}

// POST /api/licenses/renew  { serial[, edition][, trial_days][, validity_days] }
// One-click renewal: same site/customer/fingerprint, fresh serial + fresh
// validity. vendor_admin any serial; clients only their own site.
// Optional edition change (upgrade/downgrade) is vendor_admin only; clients
// renewing keep their current edition. Validity presets follow the same
// rules as issuance (trial_days 1..365 for trials, validity_days 1..1095
// for paid, vendor_admin only).
export async function handleRenewLicence(
  req: Request,
  env: Record<string, string | undefined>,
  accounts: AccountsStore,
  caller: PublicUser
): Promise<Response> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return json({ error: "invalid json" }, 400);
  }
  const serial = typeof (body as Record<string, unknown>).serial === "string"
    ? ((body as Record<string, unknown>).serial as string).trim()
    : "";
  if (!serial) return json({ error: "serial required" }, 400);
  const priv = (env.LICENSE_PRIVKEY_B64 ?? "").trim();
  if (!priv) {
    return json({ error: "licence signing not configured (LICENSE_PRIVKEY_B64)" }, 501);
  }
  const rows = await accounts.listLicences(
    caller.role === "vendor_admin" ? undefined : (caller.site_id ?? "__none__")
  );
  const prev = rows.find((r) => r.serial === serial);
  if (!prev) return json({ error: "unknown serial (or not your site)" }, 404);
  const b = body as Record<string, unknown>;
  let edition = prev.edition;
  let planChange = "";
  const wantEdition = typeof b.edition === "string" ? b.edition.trim() : "";
  if (wantEdition && wantEdition !== prev.edition) {
    if (caller.role !== "vendor_admin") {
      return json({ error: "plan changes are vendor admin only" }, 403);
    }
    edition = wantEdition;
    planChange = ` (plan-change ${prev.edition}→${edition})`;
  }
  const num = (k: string): number | undefined =>
    typeof b[k] === "number" && Number.isInteger(b[k]) ? (b[k] as number) : undefined;
  const trialDays = edition === "trial" ? num("trial_days") : undefined;
  let validityDays: number | undefined;
  if (edition !== "trial" && caller.role === "vendor_admin") {
    const v = num("validity_days");
    if (v !== undefined) {
      if (v < 1 || v > 1095) return json({ error: "validity_days must be 1..1095" }, 400);
      validityDays = v;
    }
  }
  try {
    const lic = await issueLicence(
      {
        customer: prev.customer || prev.site_id,
        site_id: prev.site_id,
        edition,
        packager_fp: prev.packager_fp,
        ...(trialDays !== undefined ? { trial_days: trialDays } : {}),
        ...(validityDays !== undefined ? { validity_days: validityDays } : {}),
      },
      priv
    );
    await accounts.logLicence({
      site_id: lic.site_id,
      customer: lic.customer,
      serial: lic.serial,
      edition: lic.edition,
      packager_fp: lic.packager_fp,
      max_stations: lic.max_stations,
      issued: lic.issued,
      expires: lic.expires,
      max_version: lic.max_version,
      issued_by: `${caller.email} (renew ${serial}${planChange})`,
    });
    await queueOffer(env, lic, lic);
    return json({ ok: true, licence: lic, renewed_from: serial }, 201);
  } catch (e) {
    return json({ error: e instanceof Error ? e.message : "renewal failed" }, 400);
  }
}

// GET /api/licenses — issuance log. Admin sees all; clients see their own site.
export async function handleListLicences(
  accounts: AccountsStore,
  caller: PublicUser
): Promise<Response> {
  const rows = await accounts.listLicences(caller.role === "vendor_admin" ? undefined : (caller.site_id ?? "__none__"));
  return json({ ok: true, licences: rows });
}
