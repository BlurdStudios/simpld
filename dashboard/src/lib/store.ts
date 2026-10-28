// Storage abstraction: KV/D1 in production, in-memory fallback in dev.
// TODO(dev): wire D1 binding for 2yr retention pruning queries
// (DELETE FROM day_reports WHERE day < date('now','-2 years') keeping summaries).
// Until then the in-memory fallback holds only the latest pushes per process.

import type { DayReport, OrderProgress, SiteSnapshot, StockCounts } from "./contract";
import type { AccountsStore } from "./db";

export interface PushStore {
  saveDay(site_id: string, report: DayReport): Promise<void>;
  saveOrders(site_id: string, orders: OrderProgress[], stock: StockCounts): Promise<void>;
  loadSite(site_id: string): Promise<SiteSnapshot | null>;
}

const mem = new Map<string, SiteSnapshot>();

export function memoryStore(): PushStore {
  return {
    async saveDay(site_id, report) {
      const cur = mem.get(site_id) ?? {
        site_id,
        pushed_at: new Date().toISOString(),
        days: {},
        orders: [],
        stock_counts: null,
      };
      cur.days[report.day] = report;
      cur.pushed_at = new Date().toISOString();
      mem.set(site_id, cur);
    },
    async saveOrders(site_id, orders, stock) {
      const cur = mem.get(site_id) ?? {
        site_id,
        pushed_at: new Date().toISOString(),
        days: {},
        orders: [],
        stock_counts: null,
      };
      cur.orders = orders;
      cur.stock_counts = stock;
      cur.pushed_at = new Date().toISOString();
      mem.set(site_id, cur);
    },
    async loadSite(site_id) {
      return mem.get(site_id) ?? null;
    },
  };
}

interface KVLike {
  get(key: string): Promise<string | null>;
  put(key: string, value: string): Promise<void>;
}

/** KV-backed store. Key layout: `site:<site_id>` -> SiteSnapshot JSON. */
export function kvStore(kv: KVLike): PushStore {
  const load = async (site_id: string): Promise<SiteSnapshot | null> => {
    const raw = await kv.get(`site:${site_id}`);
    return raw ? (JSON.parse(raw) as SiteSnapshot) : null;
  };
  const save = (s: SiteSnapshot) => kv.put(`site:${s.site_id}`, JSON.stringify(s));
  return {
    async saveDay(site_id, report) {
      const cur = (await load(site_id)) ?? {
        site_id,
        pushed_at: new Date().toISOString(),
        days: {},
        orders: [],
        stock_counts: null,
      };
      cur.days[report.day] = report;
      cur.pushed_at = new Date().toISOString();
      await save(cur);
    },
    async saveOrders(site_id, orders, stock) {
      const cur = (await load(site_id)) ?? {
        site_id,
        pushed_at: new Date().toISOString(),
        days: {},
        orders: [],
        stock_counts: null,
      };
      cur.orders = orders;
      cur.stock_counts = stock;
      cur.pushed_at = new Date().toISOString();
      await save(cur);
    },
    loadSite: load,
  };
}

/** Pick KV when the PUSHES binding exists, else in-memory (dev). */
export function storeForEnv(env: Record<string, unknown>): PushStore {
  const kv = env.PUSHES as KVLike | undefined;
  if (kv && typeof kv.get === "function" && typeof kv.put === "function") return kvStore(kv);
  return memoryStore();
}

// ---------- v2: reporter log (push-side station sightings, feeds admin Sites view) ----------

export interface ReporterEntry {
  station_id: string;
  packager_fp: string;
  seen_at: string;
}

export interface ReporterLog {
  touchPush(site_id: string, at: string): Promise<void>;
  record(site_id: string, entry: ReporterEntry): Promise<void>;
  history(site_id: string, limit?: number): Promise<ReporterEntry[]>;
}

const memEvents = new Map<string, ReporterEntry[]>();
const memPushes = new Map<string, string>();

export function memoryReporterLog(): ReporterLog {
  return {
    async touchPush(site_id, at) {
      memPushes.set(site_id, at);
      if (!memEvents.has(site_id)) memEvents.set(site_id, []);
    },
    async record(site_id, entry) {
      const arr = memEvents.get(site_id) ?? [];
      arr.unshift(entry);
      memEvents.set(site_id, arr.slice(0, 200));
      memPushes.set(site_id, entry.seen_at);
    },
    async history(site_id, limit = 50) {
      return (memEvents.get(site_id) ?? []).slice(0, limit);
    },
  };
}

/** D1-backed log: delegates to the accounts store tables (sites/reporter_events). */
export function dbReporterLog(accounts: AccountsStore): ReporterLog {
  return {
    touchPush: (site_id, at) => accounts.touchSite(site_id, at),
    record: (site_id, entry) => accounts.recordReporter({ site_id, ...entry }),
    history: (site_id, limit) => accounts.reporterHistory(site_id, limit),
  };
}

/** D1 table log when the DB binding exists, memory fallback otherwise (dev). */
export function reportersForEnv(env: Record<string, unknown>, accounts: AccountsStore): ReporterLog {
  const db = env.DB as { prepare?: unknown } | undefined;
  if (db && typeof db.prepare === "function") return dbReporterLog(accounts);
  return memoryReporterLog();
}
