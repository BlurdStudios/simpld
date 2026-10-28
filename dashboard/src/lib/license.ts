// Licence format mirror of factory/license.py — the format is FROZEN and is
// mirrored EXACTLY here. Do not invent a new format.
//
//   keys: serial, customer, site_id, edition, packager_fp, max_stations,
//         features, issued, expires, max_version, sig
//   sig: base64-std Ed25519 over the canonical JSON of all other keys
//        (sorted keys, compact separators, ensure_ascii, utf-8).
//   Private key stays offline with the vendor (LICENSE_PRIVKEY_B64 secret);
//   the public key ships in the factory (factory/license.py PUBLIC_KEY_B64).
//
// Python parity notes (verified against tools/issue_license.py output):
// - json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True).
//   ensure_ascii escapes ", \, control chars AND every codepoint > 0x7E
//   (incl. DEL) as \uXXXX lowercase hex; astral chars become surrogate pairs.
// - base64.std (padded). number formatting: licence numbers are ints.

import type { LicencePayload } from "./contract";

export const LICENCE_KEYS = [
  "serial",
  "customer",
  "site_id",
  "edition",
  "packager_fp",
  "max_stations",
  "features",
  "issued",
  "expires",
  "max_version",
  "sig",
] as const;

export const LICENCE_EDITIONS = ["simple", "pro", "premium", "trial"] as const;
export type LicenceEdition = (typeof LICENCE_EDITIONS)[number];

export const EDITION_DEFAULT_FEATURES: Record<string, string[]> = {
  simple: [],
  pro: ["lan", "history", "summary"],
  premium: ["lan", "history", "summary", "api"],
  trial: ["lan", "history", "summary"],
};

/** Tier rules: the ONLY source of stations/features/validity. The dashboard
 *  never asks the user for these — edition implies everything. Trial
 *  defaults to 14 days; the portal may pass trial_days (1..365) to vary it. */
export const EDITION_RULES: Record<string, { stations: number; features: string[]; validityDays: number }> = {
  simple: { stations: 1, features: [], validityDays: 365 },
  pro: { stations: 3, features: ["lan", "history", "summary"], validityDays: 365 },
  premium: { stations: 5, features: ["lan", "history", "summary", "api"], validityDays: 365 },
  trial: { stations: 1, features: ["lan", "history", "summary"], validityDays: 14 },
};

export const LICENCE_MAX_VERSION = "2.x";

function addDaysISO(day: string, n: number): string {
  const d = new Date(day + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function randSerial(day: string): string {
  const bytes = new Uint8Array(2);
  crypto.getRandomValues(bytes);
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `SIMPLD-${day.replaceAll("-", "")}-${hex}`;
}

const HEX = "0123456789abcdef";

function escapeString(s: string): string {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const cp = s.charCodeAt(i);
    if (cp === 0x22) out += '\\"';
    else if (cp === 0x5c) out += "\\\\";
    else if (cp === 0x08) out += "\\b";
    else if (cp === 0x09) out += "\\t";
    else if (cp === 0x0a) out += "\\n";
    else if (cp === 0x0c) out += "\\f";
    else if (cp === 0x0d) out += "\\r";
    else if (cp < 0x20 || cp > 0x7e) {
      if (cp >= 0xd800 && cp <= 0xdbff && i + 1 < s.length) {
        const lo = s.charCodeAt(i + 1);
        if (lo >= 0xdc00 && lo <= 0xdfff) {
          out +=
            "\\u" +
            HEX[(cp >> 12) & 15] +
            HEX[(cp >> 8) & 15] +
            HEX[(cp >> 4) & 15] +
            HEX[cp & 15] +
            "\\u" +
            HEX[(lo >> 12) & 15] +
            HEX[(lo >> 8) & 15] +
            HEX[(lo >> 4) & 15] +
            HEX[lo & 15];
          i++;
          continue;
        }
      }
      out +=
        "\\u" +
        HEX[(cp >> 12) & 15] +
        HEX[(cp >> 8) & 15] +
        HEX[(cp >> 4) & 15] +
        HEX[cp & 15];
    } else {
      out += s[i];
    }
  }
  return out + '"';
}

function dumpCanonical(v: unknown): string {
  if (v === null) return "null";
  if (v === true) return "true";
  if (v === false) return "false";
  if (typeof v === "number") {
    if (!Number.isFinite(v)) throw new Error("non-finite number in licence body");
    return JSON.stringify(v);
  }
  if (typeof v === "string") return escapeString(v);
  if (Array.isArray(v)) return "[" + v.map(dumpCanonical).join(",") + "]";
  if (typeof v === "object") {
    const keys = Object.keys(v as Record<string, unknown>).sort();
    return (
      "{" +
      keys.map((k) => escapeString(k) + ":" + dumpCanonical((v as Record<string, unknown>)[k])).join(",") +
      "}"
    );
  }
  throw new Error("unsupported value in licence body");
}

/** Canonical JSON string of all keys except sig (Python sort_keys + compact). */
export function canonicalString(payload: Record<string, unknown>): string {
  const body: Record<string, unknown> = {};
  for (const k of Object.keys(payload)) if (k !== "sig") body[k] = payload[k];
  return dumpCanonical(body);
}

/** Canonical bytes (utf-8) that the signature covers. */
export function canonicalBytes(payload: Record<string, unknown>): ArrayBuffer {
  return toBuf(new TextEncoder().encode(canonicalString(payload)));
}

/** The paste-able key string: canonical compact JSON including sig. */
export function licenceKeyString(lic: LicencePayload): string {
  return canonicalString(lic as unknown as Record<string, unknown>);
}

function b64encode(raw: Uint8Array): string {
  let s = "";
  for (let i = 0; i < raw.length; i++) s += String.fromCharCode(raw[i]);
  return btoa(s);
}

function b64decode(b64: string): Uint8Array {
  const s = atob(b64.trim());
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

// Raw 32B Ed25519 seed -> PKCS#8 DER (prefix + seed) for WebCrypto import.
const PKCS8_PREFIX = new Uint8Array([
  0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20,
]);

/** Owned ArrayBuffer copy (satisfies TS 5.6 BufferSource typing). */
function toBuf(u8: Uint8Array): ArrayBuffer {
  const c = new Uint8Array(u8.length);
  c.set(u8);
  return c.buffer;
}

async function importPrivateKey(seedB64: string): Promise<CryptoKey> {
  const seed = b64decode(seedB64);
  if (seed.length !== 32) throw new Error("private key must decode to 32 bytes");
  const pkcs8 = new Uint8Array(PKCS8_PREFIX.length + 32);
  pkcs8.set(PKCS8_PREFIX, 0);
  pkcs8.set(seed, PKCS8_PREFIX.length);
  return crypto.subtle.importKey("pkcs8", toBuf(pkcs8), { name: "Ed25519" }, false, [
    "sign",
  ]);
}

async function importPublicKey(pubB64: string): Promise<CryptoKey> {
  const raw = b64decode(pubB64);
  if (raw.length !== 32) throw new Error("public key must decode to 32 bytes");
  return crypto.subtle.importKey(
    "raw",
    toBuf(raw),
    { name: "Ed25519" },
    false,
    ["verify"]
  );
}

/** Ed25519 verify only. False on any problem (never throws for a bad sig). */
export async function verifySignature(
  payload: Record<string, unknown>,
  pubkeyB64: string
): Promise<boolean> {
  try {
    const pub = await importPublicKey(pubkeyB64);
    const sig = b64decode(String((payload as Record<string, unknown>).sig ?? ""));
    const ok = await crypto.subtle.verify("Ed25519", pub, toBuf(sig), canonicalBytes(payload));
    return ok;
  } catch {
    return false;
  }
}

export interface IssueInput {
  serial?: string;
  customer: string;
  site_id: string;
  edition?: string;
  packager_fp: string;
  max_stations?: number;
  features?: string[];
  issued?: string; // YYYY-MM-DD, defaults to today (UTC)
  expires?: string; // YYYY-MM-DD, defaults to issued + tier validity
  max_version?: string;
  trial_days?: number; // trial only: 1..365, overrides rule.validityDays
}

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Build + sign a licence. Tier rules derive stations/features/validity from
 *  the edition — explicit max_stations/features/issued/expires are accepted
 *  (offline vendor CLI compat, tests) but the portal endpoint never forwards
 *  them: the server owns every field except site/customer/fingerprint. */
export async function issueLicence(input: IssueInput, privkeyB64: string): Promise<LicencePayload> {
  const edition = (input.edition ?? "pro").trim();
  const rule = EDITION_RULES[edition];
  if (!rule) {
    throw new Error(`unknown edition '${edition}'`);
  }
  let maxStations = input.max_stations ?? rule.stations;
  if (edition === "simple") maxStations = 1;
  if (edition === "trial") maxStations = 1;
  if (!Number.isInteger(maxStations) || maxStations < 1 || maxStations > 20) {
    throw new Error("max_stations must be an integer 1..20");
  }
  const issued = ((input.issued ?? todayISO()).slice(0, 10));
  if (!/^\d{4}-\d{2}-\d{2}$/.test(issued)) throw new Error("issued must be YYYY-MM-DD");
  let validity = rule.validityDays;
  if (edition === "trial" && input.trial_days !== undefined) {
    if (!Number.isInteger(input.trial_days) || input.trial_days < 1 || input.trial_days > 365) {
      throw new Error("trial_days must be 1..365");
    }
    validity = input.trial_days;
  }
  const expires = input.expires ? input.expires.slice(0, 10) : addDaysISO(issued, validity);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(expires)) throw new Error("expires must be YYYY-MM-DD");
  if (!input.site_id.trim()) throw new Error("site_id required");
  if (!input.packager_fp.trim()) throw new Error("packager_fp required");
  if (!input.customer.trim()) throw new Error("customer required");
  const body: Record<string, unknown> = {
    serial: input.serial?.trim() || randSerial(issued),
    customer: input.customer.trim(),
    site_id: input.site_id.trim(),
    edition,
    packager_fp: input.packager_fp.trim(),
    max_stations: maxStations,
    features: input.features ?? [...(EDITION_DEFAULT_FEATURES[edition] ?? [])],
    issued,
    expires,
    max_version: (input.max_version ?? LICENCE_MAX_VERSION).trim() || LICENCE_MAX_VERSION,
  };
  const priv = await importPrivateKey(privkeyB64);
  const sig = await crypto.subtle.sign("Ed25519", priv, canonicalBytes(body));
  return { ...(body as unknown as LicencePayload), sig: b64encode(new Uint8Array(sig)) };
}

export interface VerifyOpts {
  pubkeyB64: string;
  /** Board fingerprint to bind against. Skip only when just checking shape+sig. */
  machineFp?: string;
  /** App version gate, e.g. "2.0.1" (mirrors factory max_version check). */
  appVersion?: string;
}

function parseDay(s: unknown): string | null {
  const t = String(s ?? "").slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(t) ? t : null;
}

/** Full offline check: shape + sig + board binding + version gate. Mirrors verify_license. */
export async function verifyLicence(
  lic: unknown,
  opts: VerifyOpts
): Promise<{ ok: boolean; reason: string }> {
  if (typeof lic !== "object" || lic === null) return { ok: false, reason: "no licence file — contact admin" };
  const l = lic as Record<string, unknown>;
  for (const k of LICENCE_KEYS) {
    if (l[k] === undefined || l[k] === null || l[k] === "") {
      return { ok: false, reason: `licence missing '${k}' — contact admin` };
    }
  }
  if (!(LICENCE_EDITIONS as readonly string[]).includes(String(l.edition))) {
    return { ok: false, reason: "licence edition unknown — contact admin" };
  }
  const ms = Number(l.max_stations);
  if (!Number.isInteger(ms) || ms < 1 || ms > 20) {
    return { ok: false, reason: "licence station count invalid — contact admin" };
  }
  if (!Array.isArray(l.features)) return { ok: false, reason: "licence features invalid — contact admin" };
  if (!parseDay(l.issued)) return { ok: false, reason: "licence issue date invalid — contact admin" };
  if (!parseDay(l.expires)) return { ok: false, reason: "licence expiry invalid — contact admin" };
  if (!(await verifySignature(l, opts.pubkeyB64))) {
    return { ok: false, reason: "licence signature invalid — contact admin" };
  }
  if (opts.machineFp !== undefined) {
    const fp = String(l.packager_fp ?? "").trim();
    if (fp && fp !== opts.machineFp) {
      return { ok: false, reason: "licence is bound to another computer (packager_fp mismatch) — contact admin" };
    }
  }
  if (opts.appVersion) {
    const mv = String(l.max_version ?? "").trim();
    const major = mv.split(".")[0].replace(/^v/, "");
    const cur = opts.appVersion.split(".")[0];
    if (/^\d+$/.test(major) && /^\d+$/.test(cur) && Number(cur) > Number(major)) {
      return { ok: false, reason: `licence covers v${major}.x but app is v${opts.appVersion} — contact admin` };
    }
  }
  if (String(l.edition) === "simple" && (l.features as unknown[]).includes("lan")) {
    return { ok: false, reason: "simple edition has no LAN — contact admin" };
  }
  if (String(l.edition) === "trial" && Number(l.max_stations) !== 1) {
    return { ok: false, reason: "trial licence is single-station — contact admin" };
  }
  return { ok: true, reason: "ok" };
}

export interface StatusOpts extends VerifyOpts {
  today?: string; // YYYY-MM-DD, defaults to today (UTC)
}

/** State machine: ok / expiring / grace / blocked / invalid / none. Mirrors license_status. */
export async function licenceStatus(
  lic: LicencePayload | null,
  opts: StatusOpts
): Promise<{ state: string; reason: string; expires?: string; lic?: LicencePayload }> {
  if (!lic) return { state: "none", reason: "no licence file — contact admin" };
  const v = await verifyLicence(lic, opts);
  if (!v.ok) return { state: "invalid", reason: v.reason };
  const exp = String(lic.expires).slice(0, 10);
  const now = (opts.today ?? todayISO()).slice(0, 10);
  if (now <= exp) {
    const left = Math.round((Date.parse(exp) - Date.parse(now)) / 86400000);
    if (left <= 14) return { state: "expiring", reason: `expires in ${left}d`, expires: exp, lic };
    return { state: "ok", reason: "ok", expires: exp, lic };
  }
  const over = Math.round((Date.parse(now) - Date.parse(exp)) / 86400000);
  if (over <= 30) {
    return { state: "grace", reason: `expired ${over}d ago — ${30 - over}d grace left, contact admin`, expires: exp, lic };
  }
  return {
    state: "blocked",
    reason: `expired ${over}d ago — writes blocked, backup/export still allowed`,
    expires: exp,
    lic,
  };
}
