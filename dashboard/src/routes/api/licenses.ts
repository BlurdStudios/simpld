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
  try {
    // NOTE: max_stations/features/issued/expires/serial/max_version from the
    // client are deliberately IGNORED — tier rules derive them. Forged tier
    // values (e.g. max_stations 99 on a simple licence) can never sign.
    // Exception: trial_days (1..365) is honoured for trial licences only.
    const edition = str("edition") ?? "pro";
    const trialDays = edition === "trial" ? num("trial_days") : undefined;
    const lic = await issueLicence(
      {
        customer: str("customer") ?? "",
        site_id: siteId,
        edition,
        packager_fp: str("packager_fp") ?? "",
        ...(trialDays !== undefined ? { trial_days: trialDays } : {}),
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
      issued_by: caller.email,
    });
    return json({ ok: true, licence: lic }, 201);
  } catch (e) {
    return json({ error: e instanceof Error ? e.message : "issuance failed" }, 400);
  }
}

// POST /api/licenses/renew  { serial }  (session auth)
// One-click renewal: same site/customer/fingerprint/edition, fresh serial +
// fresh 1-year validity. vendor_admin any serial; clients only their own site.
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
  try {
    const lic = await issueLicence(
      {
        customer: prev.customer || prev.site_id,
        site_id: prev.site_id,
        edition: prev.edition,
        packager_fp: prev.packager_fp,
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
      issued_by: `${caller.email} (renew ${serial})`,
    });
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
