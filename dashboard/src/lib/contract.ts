// Shared contract types. Source of truth: SYNC_CONTRACT.md (v2).
// Factory's factory/sync.py build_push() MUST produce these shapes.
// v2 additions (reporter extension, licences, releases) are OPTIONAL on
// push payloads: old validators keep passing (unknown fields ignored).

export interface ReelRow {
  roll_code: string;
  roll_label: string;
  job_id: string;
  net_kg: number;
}

export interface BoxRow {
  carton_no: string;
  bundle_no: string | null;
  job_id: string;
  num_reels: number;
  total_gross_kg: number;
  linked_rolls: string[];
}

export interface DayReport {
  day: string; // YYYY-MM-DD
  reel_rows: ReelRow[];
  box_rows: BoxRow[];
  totals: {
    reel_count: number;
    reel_net_kg: number;
    box_count: number;
    box_gross_kg: number;
  };
  by_job: Record<string, { reels: number; reel_net_kg: number; boxes: number; box_gross_kg: number }>;
}

export interface OrderProgress {
  order_id: string;
  buyer: string;
  target_qty: number;
  loaded_qty: number;
  status: string;
}

export interface StockCounts {
  raw_rolls: number;
  packed_cartons: number;
  dispatched_cartons: number;
}

export interface PushDayPayload {
  site_id: string;
  day_report: DayReport;
}

export interface PushOrdersPayload {
  site_id: string;
  orders: OrderProgress[];
  stock_counts: StockCounts;
}

export interface SiteSnapshot {
  site_id: string;
  pushed_at: string; // ISO timestamp of last push
  days: Record<string, DayReport>; // keyed by day YYYY-MM-DD
  orders: OrderProgress[];
  stock_counts: StockCounts | null;
}

export function validateDayReport(v: unknown): v is DayReport {
  if (typeof v !== "object" || v === null) return false;
  const d = v as Record<string, unknown>;
  if (typeof d.day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(d.day)) return false;
  if (!Array.isArray(d.reel_rows) || !Array.isArray(d.box_rows)) return false;
  const t = d.totals as Record<string, unknown> | undefined;
  if (
    typeof t !== "object" || t === null ||
    typeof t.reel_count !== "number" || typeof t.reel_net_kg !== "number" ||
    typeof t.box_count !== "number" || typeof t.box_gross_kg !== "number"
  )
    return false;
  if (typeof d.by_job !== "object" || d.by_job === null) return false;
  return true;
}

export function validateOrdersPayload(v: unknown): v is { orders: OrderProgress[]; stock_counts: StockCounts } {
  if (typeof v !== "object" || v === null) return false;
  const d = v as Record<string, unknown>;
  if (!Array.isArray(d.orders)) return false;
  const s = d.stock_counts as Record<string, unknown> | undefined;
  if (
    typeof s !== "object" || s === null ||
    typeof s.raw_rolls !== "number" ||
    typeof s.packed_cartons !== "number" ||
    typeof s.dispatched_cartons !== "number"
  )
    return false;
  for (const o of d.orders as unknown[]) {
    const r = o as Record<string, unknown>;
    if (typeof r.order_id !== "string" || typeof r.buyer !== "string") return false;
    if (typeof r.target_qty !== "number" || typeof r.loaded_qty !== "number") return false;
  }
  return true;
}

// ---------- v2: reporter extension (optional, backward compatible) ----------
// Pushes may carry `reporter` (which station sent this push) and `stations`
// (heartbeats). Validators above ignore unknown fields, so old factory builds
// keep passing; the Worker persists what it can and ignores the rest.

export interface ReporterInfo {
  station_id?: string;
  packager_fp?: string;
}

export interface StationHeartbeat {
  station_id: string;
  packager_fp?: string;
  last_seen?: string;
}

export interface PushDayPayloadV2 extends PushDayPayload {
  reporter?: ReporterInfo;
  stations?: StationHeartbeat[];
}

export interface PushOrdersPayloadV2 {
  site_id: string;
  orders: OrderProgress[];
  stock_counts: StockCounts;
  reporter?: ReporterInfo;
  stations?: StationHeartbeat[];
}

/** Lenient extraction: returns string fields when present, never throws. */
export function extractReporter(body: unknown): { station_id: string; packager_fp: string } | null {
  if (typeof body !== "object" || body === null) return null;
  const r = (body as Record<string, unknown>).reporter;
  if (typeof r !== "object" || r === null) return null;
  const rec = r as Record<string, unknown>;
  const station_id = typeof rec.station_id === "string" ? rec.station_id.trim() : "";
  const packager_fp = typeof rec.packager_fp === "string" ? rec.packager_fp.trim() : "";
  if (!station_id && !packager_fp) return null;
  return { station_id, packager_fp };
}

/** Lenient extraction of the factory licence snapshot (skips when absent
 *  or malformed — old factories omit it and push on regardless). */
export function extractSiteLicence(body: unknown): {
  edition: string; expires: string; days_left: number | null; state: string;
  app_version: string; packager_fp: string;
} | null {
  if (typeof body !== "object" || body === null) return null;
  const l = (body as Record<string, unknown>).licence;
  if (typeof l !== "object" || l === null) return null;
  const rec = l as Record<string, unknown>;
  const str = (v: unknown): string => (typeof v === "string" ? v.trim().slice(0, 64) : "");
  const days =
    typeof rec.days_left === "number" && Number.isInteger(rec.days_left) &&
    rec.days_left > -10000 && rec.days_left < 10000 ? rec.days_left : null;
  const out = {
    edition: str(rec.edition), expires: str(rec.expires), days_left: days,
    state: str(rec.state), app_version: str(rec.app_version),
    packager_fp: str(rec.packager_fp),
  };
  if (!out.edition && !out.state) return null;
  return out;
}

/** Lenient extraction of stations[] heartbeats (skips malformed entries). */
export function extractStations(body: unknown): Array<{ station_id: string; packager_fp: string }> {
  if (typeof body !== "object" || body === null) return [];
  const s = (body as Record<string, unknown>).stations;
  if (!Array.isArray(s)) return [];
  const out: Array<{ station_id: string; packager_fp: string }> = [];
  for (const e of s) {
    if (typeof e !== "object" || e === null) continue;
    const rec = e as Record<string, unknown>;
    if (typeof rec.station_id !== "string" || !rec.station_id.trim()) continue;
    out.push({
      station_id: rec.station_id.trim(),
      packager_fp: typeof rec.packager_fp === "string" ? rec.packager_fp.trim() : "",
    });
  }
  return out;
}

// ---------- v2: licence payload (frozen format, mirrors factory/license.py) ----------

export interface LicencePayload {
  serial: string;
  customer: string;
  site_id: string;
  edition: string;
  packager_fp: string;
  max_stations: number;
  features: string[];
  issued: string;
  expires: string;
  max_version: string;
  sig?: string;
}

// ---------- v2: release manifest (reuses UPDATES.md latest.json shape) ----------

export interface ReleaseManifest {
  version: string;
  file: string;
  sha256: string;
  notes: string;
}

export function validateReleaseManifest(v: unknown): v is ReleaseManifest {
  if (typeof v !== "object" || v === null) return false;
  const d = v as Record<string, unknown>;
  return (
    typeof d.version === "string" &&
    /^\d+(\.\d+)*$/.test(d.version) &&
    typeof d.file === "string" &&
    /^Simpld-Setup-\d+(\.\d+)*\.exe$/.test(d.file) &&
    typeof d.sha256 === "string" &&
    /^[0-9a-f]{64}$/.test(d.sha256) &&
    typeof d.notes === "string"
  );
}
