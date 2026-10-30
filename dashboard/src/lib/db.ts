// Accounts + API keys + licence log + reporter registry.
// Backing store: D1 when the DB binding exists, module-level memory otherwise
// (dev/smoke). Both implement AccountsStore so handlers stay backend-agnostic.

import { hashPassword, newId, newToken, nowIso, sha256Hex, verifyPassword } from "./password";

export type Role = "vendor_admin" | "client";

export interface User {
  id: string;
  email: string;
  pass_hash: string;
  role: Role;
  site_id: string | null;
  disabled: number;
  created_at: string;
}

export interface PublicUser {
  id: string;
  email: string;
  role: Role;
  site_id: string | null;
  disabled: boolean;
  created_at: string;
}

export interface ApiKeyRow {
  id: string;
  user_id: string;
  site_id: string;
  key_hash: string;
  label: string;
  created_at: string;
  revoked_at: string | null;
}

export interface LicenceLogRow {
  id: string;
  site_id: string;
  customer: string;
  serial: string;
  edition: string;
  packager_fp: string;
  max_stations: number;
  issued: string;
  expires: string;
  max_version: string;
  issued_by: string;
  created_at: string;
}

export interface ReporterEvent {
  site_id: string;
  station_id: string;
  packager_fp: string;
  seen_at: string;
}

/** Live licence snapshot pushed by the factory (one row per site, latest
 *  push wins). Powers the upgrade/trial-nudge tracking view. */
export interface SiteLicenceRow {
  site_id: string;
  edition: string;
  expires: string;
  days_left: number | null;
  state: string;
  app_version: string;
  packager_fp: string;
  updated_at: string;
}

export interface SiteOverview {
  site_id: string;
  last_push_at: string | null;
  last_reporter_fp: string | null;
  frozen: boolean;
  active_keys: number;
  reporters_seen: number;
}

export const SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  pass_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('vendor_admin','client')),
  site_id TEXT,
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  site_id TEXT NOT NULL,
  key_hash TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS licences (
  id TEXT PRIMARY KEY,
  site_id TEXT NOT NULL,
  customer TEXT NOT NULL DEFAULT '',
  serial TEXT NOT NULL,
  edition TEXT NOT NULL,
  packager_fp TEXT NOT NULL,
  max_stations INTEGER NOT NULL,
  issued TEXT NOT NULL,
  expires TEXT NOT NULL,
  max_version TEXT NOT NULL,
  issued_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reporter_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id TEXT NOT NULL,
  station_id TEXT NOT NULL,
  packager_fp TEXT NOT NULL,
  seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reporter_site ON reporter_events(site_id, seen_at);
CREATE TABLE IF NOT EXISTS sites (
  site_id TEXT PRIMARY KEY,
  last_push_at TEXT,
  last_reporter_fp TEXT,
  frozen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS site_licences (
  site_id TEXT PRIMARY KEY,
  edition TEXT NOT NULL DEFAULT '',
  expires TEXT NOT NULL DEFAULT '',
  days_left INTEGER,
  state TEXT NOT NULL DEFAULT '',
  app_version TEXT NOT NULL DEFAULT '',
  packager_fp TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT ''
);
`.trim();

export interface D1Prepared {
  bind(...params: unknown[]): {
    first<T>(): Promise<T | null>;
    all<T>(): Promise<{ results: T[] }>;
    run(): Promise<unknown>;
  };
}

export interface D1Like {
  prepare(query: string): D1Prepared;
  exec(query: string): Promise<unknown>;
}

export async function initSchema(db: D1Like): Promise<void> {
  // Single-line each: some local D1 shims truncate multi-line input at the
  // first newline ("incomplete input"). Real D1 accepts both forms.
  for (const raw of SCHEMA_SQL.split(";")) {
    const stmt = raw.replace(/\s+/g, " ").trim();
    if (stmt) await db.exec(stmt);
  }
}

export function toPublic(u: User): PublicUser {
  return {
    id: u.id,
    email: u.email,
    role: u.role,
    site_id: u.site_id,
    disabled: u.disabled === 1,
    created_at: u.created_at,
  };
}

export interface AccountsStore {
  userCount(): Promise<number>;
  createUser(email: string, password: string, role: Role, siteId: string | null): Promise<PublicUser>;
  findUserByEmail(email: string): Promise<User | null>;
  findUserById(id: string): Promise<User | null>;
  listUsers(): Promise<PublicUser[]>;
  updateUser(id: string, patch: { role?: Role; site_id?: string | null; disabled?: boolean }): Promise<PublicUser | null>;
  verifyLogin(email: string, password: string): Promise<PublicUser | null>;
  createSession(userId: string, days?: number): Promise<{ token: string; expires: string }>;
  sessionUser(token: string): Promise<PublicUser | null>;
  deleteSession(token: string): Promise<void>;
  /** Create a key; revokes the user's other active keys first (one active key per user). */
  createApiKey(userId: string, siteId: string, label: string): Promise<{ row: ApiKeyRow; plaintext: string; revokedCount: number }>;
  listApiKeys(userId?: string): Promise<Array<Omit<ApiKeyRow, "key_hash">>>;
  revokeApiKey(id: string): Promise<boolean>;
  revokeSiteKeys(siteId: string): Promise<number>;
  /** Resolve an unrevoked key to its site. Null when unknown/revoked. */
  siteForKeyHash(keyHash: string): Promise<string | null>;
  touchSite(siteId: string, at: string): Promise<void>;
  recordReporter(ev: ReporterEvent): Promise<void>;
  reporterHistory(siteId: string, limit?: number): Promise<ReporterEvent[]>;
  sitesOverview(): Promise<SiteOverview[]>;
  setSiteFrozen(siteId: string, frozen: boolean): Promise<void>;
  logLicence(row: Omit<LicenceLogRow, "id" | "created_at">): Promise<LicenceLogRow>;
  listLicences(siteId?: string): Promise<LicenceLogRow[]>;
  upsertSiteLicence(row: SiteLicenceRow): Promise<void>;
  listSiteLicences(): Promise<SiteLicenceRow[]>;
}

const SESSION_DAYS = 7;

export class D1AccountsStore implements AccountsStore {
  protected db: D1Like;
  constructor(db: D1Like) {
    this.db = db;
  }

  async userCount(): Promise<number> {
    const r = await this.db.prepare("SELECT COUNT(*) AS n FROM users").bind().first<{ n: number }>();
    return Number(r?.n ?? 0);
  }

  async createUser(email: string, password: string, role: Role, siteId: string | null): Promise<PublicUser> {
    const em = email.trim().toLowerCase();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(em)) throw new Error("invalid email");
    if (role !== "vendor_admin" && role !== "client") throw new Error("invalid role");
    if (role === "client" && !siteId?.trim()) throw new Error("client users require a site_id");
    const row: User = {
      id: newId("u"),
      email: em,
      pass_hash: await hashPassword(password),
      role,
      site_id: role === "client" ? siteId!.trim() : siteId?.trim() || null,
      disabled: 0,
      created_at: nowIso(),
    };
    try {
      await this.db
        .prepare("INSERT INTO users(id,email,pass_hash,role,site_id,disabled,created_at) VALUES(?,?,?,?,?,?,?)")
        .bind(row.id, row.email, row.pass_hash, row.role, row.site_id, row.disabled, row.created_at)
        .run();
    } catch {
      throw new Error("email already exists");
    }
    return toPublic(row);
  }

  async findUserByEmail(email: string): Promise<User | null> {
    return this.db.prepare("SELECT * FROM users WHERE email=?").bind(email.trim().toLowerCase()).first<User>();
  }

  async findUserById(id: string): Promise<User | null> {
    return this.db.prepare("SELECT * FROM users WHERE id=?").bind(id).first<User>();
  }

  async listUsers(): Promise<PublicUser[]> {
    const r = await this.db.prepare("SELECT * FROM users ORDER BY created_at").bind().all<User>();
    return r.results.map(toPublic);
  }

  async updateUser(id: string, patch: { role?: Role; site_id?: string | null; disabled?: boolean }): Promise<PublicUser | null> {
    const cur = await this.findUserById(id);
    if (!cur) return null;
    const role = patch.role ?? cur.role;
    if (role !== "vendor_admin" && role !== "client") throw new Error("invalid role");
    let siteId: string | null = patch.site_id !== undefined ? patch.site_id : cur.site_id;
    if (role === "client" && !siteId?.trim()) throw new Error("client users require a site_id");
    if (role === "vendor_admin" && patch.site_id === undefined) siteId = cur.site_id;
    const disabled = patch.disabled !== undefined ? (patch.disabled ? 1 : 0) : cur.disabled;
    await this.db
      .prepare("UPDATE users SET role=?, site_id=?, disabled=? WHERE id=?")
      .bind(role, role === "client" ? siteId!.trim() : siteId?.trim() || null, disabled, id)
      .run();
    const next = await this.findUserById(id);
    return next ? toPublic(next) : null;
  }

  async verifyLogin(email: string, password: string): Promise<PublicUser | null> {
    const u = await this.findUserByEmail(email);
    if (!u || u.disabled === 1) return null;
    if (!(await verifyPassword(password, u.pass_hash))) return null;
    return toPublic(u);
  }

  async createSession(userId: string, days = SESSION_DAYS): Promise<{ token: string; expires: string }> {
    const token = newToken();
    const expires = new Date(Date.now() + days * 86400000).toISOString();
    await this.db.prepare("INSERT INTO sessions(token,user_id,expires) VALUES(?,?,?)").bind(token, userId, expires).run();
    return { token, expires };
  }

  async sessionUser(token: string): Promise<PublicUser | null> {
    if (!token) return null;
    const s = await this.db.prepare("SELECT * FROM sessions WHERE token=?").bind(token).first<{ user_id: string; expires: string }>();
    if (!s || s.expires <= nowIso()) {
      if (s) await this.deleteSession(token);
      return null;
    }
    const u = await this.findUserById(s.user_id);
    if (!u || u.disabled === 1) return null;
    return toPublic(u);
  }

  async deleteSession(token: string): Promise<void> {
    await this.db.prepare("DELETE FROM sessions WHERE token=?").bind(token).run();
  }

  async createApiKey(userId: string, siteId: string, label: string): Promise<{ row: ApiKeyRow; plaintext: string; revokedCount: number }> {
    const u = await this.findUserById(userId);
    if (!u) throw new Error("unknown user");
    if (!siteId.trim()) throw new Error("site_id required");
    const { newApiKey } = await import("./password");
    const plaintext = newApiKey();
    const keyHash = await sha256Hex(plaintext);
    const at = nowIso();
    const act = await this.db
      .prepare("SELECT id FROM api_keys WHERE user_id=? AND revoked_at IS NULL")
      .bind(userId)
      .all<{ id: string }>();
    for (const r of act.results) {
      await this.db.prepare("UPDATE api_keys SET revoked_at=? WHERE id=?").bind(at, r.id).run();
    }
    const row: ApiKeyRow = {
      id: newId("k"),
      user_id: userId,
      site_id: siteId.trim(),
      key_hash: keyHash,
      label: label.trim().slice(0, 80),
      created_at: at,
      revoked_at: null,
    };
    await this.db
      .prepare("INSERT INTO api_keys(id,user_id,site_id,key_hash,label,created_at,revoked_at) VALUES(?,?,?,?,?,?,?)")
      .bind(row.id, row.user_id, row.site_id, row.key_hash, row.label, row.created_at, row.revoked_at)
      .run();
    await this.touchSite(row.site_id, at);
    return { row, plaintext, revokedCount: act.results.length };
  }

  async listApiKeys(userId?: string): Promise<Array<Omit<ApiKeyRow, "key_hash">>> {
    const r = userId
      ? await this.db.prepare("SELECT id,user_id,site_id,label,created_at,revoked_at FROM api_keys WHERE user_id=? ORDER BY created_at DESC").bind(userId).all<Omit<ApiKeyRow, "key_hash">>()
      : await this.db.prepare("SELECT id,user_id,site_id,label,created_at,revoked_at FROM api_keys ORDER BY created_at DESC").bind().all<Omit<ApiKeyRow, "key_hash">>();
    return r.results;
  }

  async revokeApiKey(id: string): Promise<boolean> {
    const cur = await this.db.prepare("SELECT * FROM api_keys WHERE id=?").bind(id).first<ApiKeyRow>();
    if (!cur || cur.revoked_at) return false;
    await this.db.prepare("UPDATE api_keys SET revoked_at=? WHERE id=?").bind(nowIso(), id).run();
    return true;
  }

  async revokeSiteKeys(siteId: string): Promise<number> {
    const act = await this.db
      .prepare("SELECT id FROM api_keys WHERE site_id=? AND revoked_at IS NULL")
      .bind(siteId)
      .all<{ id: string }>();
    const at = nowIso();
    for (const r of act.results) {
      await this.db.prepare("UPDATE api_keys SET revoked_at=? WHERE id=?").bind(at, r.id).run();
    }
    return act.results.length;
  }

  async siteForKeyHash(keyHash: string): Promise<string | null> {
    const r = await this.db
      .prepare("SELECT site_id FROM api_keys WHERE key_hash=? AND revoked_at IS NULL")
      .bind(keyHash)
      .first<{ site_id: string }>();
    return r?.site_id ?? null;
  }

  async touchSite(siteId: string, at: string): Promise<void> {
    await this.db
      .prepare("INSERT INTO sites(site_id,last_push_at,last_reporter_fp,frozen) VALUES(?,?,?,0) ON CONFLICT(site_id) DO UPDATE SET last_push_at=excluded.last_push_at")
      .bind(siteId, at, null)
      .run();
  }

  async recordReporter(ev: ReporterEvent): Promise<void> {
    await this.db
      .prepare("INSERT INTO reporter_events(site_id,station_id,packager_fp,seen_at) VALUES(?,?,?,?)")
      .bind(ev.site_id, ev.station_id, ev.packager_fp, ev.seen_at)
      .run();
    await this.db
      .prepare("INSERT INTO sites(site_id,last_push_at,last_reporter_fp,frozen) VALUES(?,?,?,0) ON CONFLICT(site_id) DO UPDATE SET last_reporter_fp=excluded.last_reporter_fp, last_push_at=COALESCE(sites.last_push_at, excluded.last_push_at)")
      .bind(ev.site_id, ev.seen_at, ev.packager_fp || null)
      .run();
  }

  async reporterHistory(siteId: string, limit = 50): Promise<ReporterEvent[]> {
    const r = await this.db
      .prepare("SELECT site_id,station_id,packager_fp,seen_at FROM reporter_events WHERE site_id=? ORDER BY seen_at DESC LIMIT ?")
      .bind(siteId, limit)
      .all<ReporterEvent>();
    return r.results;
  }

  async sitesOverview(): Promise<SiteOverview[]> {
    const sites = await this.db.prepare("SELECT * FROM sites ORDER BY site_id").bind().all<{ site_id: string; last_push_at: string | null; last_reporter_fp: string | null; frozen: number }>();
    const keys = await this.db
      .prepare("SELECT site_id, COUNT(*) AS n FROM api_keys WHERE revoked_at IS NULL GROUP BY site_id")
      .bind()
      .all<{ site_id: string; n: number }>();
    const reps = await this.db
      .prepare("SELECT site_id, COUNT(DISTINCT station_id || '|' || packager_fp) AS n FROM reporter_events GROUP BY site_id")
      .bind()
      .all<{ site_id: string; n: number }>();
    const km = new Map(keys.results.map((k) => [k.site_id, Number(k.n)]));
    const rm = new Map(reps.results.map((k) => [k.site_id, Number(k.n)]));
    return sites.results.map((s) => ({
      site_id: s.site_id,
      last_push_at: s.last_push_at,
      last_reporter_fp: s.last_reporter_fp,
      frozen: s.frozen === 1,
      active_keys: km.get(s.site_id) ?? 0,
      reporters_seen: rm.get(s.site_id) ?? 0,
    }));
  }

  async setSiteFrozen(siteId: string, frozen: boolean): Promise<void> {
    await this.db
      .prepare("INSERT INTO sites(site_id,last_push_at,last_reporter_fp,frozen) VALUES(?,?,?,?) ON CONFLICT(site_id) DO UPDATE SET frozen=excluded.frozen")
      .bind(siteId, null, null, frozen ? 1 : 0)
      .run();
  }

  async logLicence(row: Omit<LicenceLogRow, "id" | "created_at">): Promise<LicenceLogRow> {
    const full: LicenceLogRow = { ...row, id: newId("lic"), created_at: nowIso() };
    await this.db
      .prepare("INSERT INTO licences(id,site_id,customer,serial,edition,packager_fp,max_stations,issued,expires,max_version,issued_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)")
      .bind(full.id, full.site_id, full.customer, full.serial, full.edition, full.packager_fp, full.max_stations, full.issued, full.expires, full.max_version, full.issued_by, full.created_at)
      .run();
    return full;
  }

  async listLicences(siteId?: string): Promise<LicenceLogRow[]> {
    const r = siteId
      ? await this.db.prepare("SELECT * FROM licences WHERE site_id=? ORDER BY created_at DESC").bind(siteId).all<LicenceLogRow>()
      : await this.db.prepare("SELECT * FROM licences ORDER BY created_at DESC").bind().all<LicenceLogRow>();
    return r.results;
  }

  async upsertSiteLicence(row: SiteLicenceRow): Promise<void> {
    await this.db
      .prepare("INSERT INTO site_licences(site_id,edition,expires,days_left,state,app_version,packager_fp,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(site_id) DO UPDATE SET edition=excluded.edition,expires=excluded.expires,days_left=excluded.days_left,state=excluded.state,app_version=excluded.app_version,packager_fp=excluded.packager_fp,updated_at=excluded.updated_at")
      .bind(row.site_id, row.edition, row.expires, row.days_left, row.state, row.app_version, row.packager_fp, row.updated_at)
      .run();
  }

  async listSiteLicences(): Promise<SiteLicenceRow[]> {
    const r = await this.db.prepare("SELECT * FROM site_licences ORDER BY updated_at DESC").bind().all<SiteLicenceRow>();
    return r.results;
  }
}

/** Dev/smoke backend: same interface, module-level memory. Never for production. */
export class MemoryAccountsStore extends D1AccountsStore {
  constructor() {
    super(new MemoryD1());
  }
}

type Row = Record<string, unknown>;

class MemoryD1 implements D1Like {
  tables: Record<string, Row[]> = {
    users: [],
    sessions: [],
    api_keys: [],
    licences: [],
    reporter_events: [],
    sites: [],
    site_licences: [],
  };

  async exec(q: string): Promise<unknown> {
    void q; // schema is implicit in memory; tables pre-created above
    return { success: true };
  }

  prepare(query: string): D1Prepared {
    const t = this.tables;
    const q = query.replace(/\s+/g, " ").trim();
    return {
      bind(...params: unknown[]) {
        const first = async <T>(): Promise<T | null> => {
          const all = await runAll<T>();
          return all[0] ?? null;
        };
        const all = async <T>(): Promise<{ results: T[] }> => ({ results: await runAll<T>() });
        const run = async (): Promise<unknown> => {
          runWrite();
          return { success: true };
        };
        const runAll = async <T>(): Promise<T[]> => {
          if (q.startsWith("SELECT COUNT(*) AS n FROM users")) return [{ n: t.users.length } as unknown as T];
          if (q.startsWith("SELECT * FROM users WHERE email=?"))
            return t.users.filter((u) => u.email === params[0]) as unknown as T[];
          if (q.startsWith("SELECT * FROM users WHERE id=?"))
            return t.users.filter((u) => u.id === params[0]) as unknown as T[];
          if (q.startsWith("SELECT * FROM users ORDER BY created_at")) return [...t.users] as unknown as T[];
          if (q.startsWith("SELECT * FROM sessions WHERE token=?"))
            return t.sessions.filter((s) => s.token === params[0]) as unknown as T[];
          if (q.startsWith("SELECT id FROM api_keys WHERE user_id=? AND revoked_at IS NULL"))
            return t.api_keys.filter((k) => k.user_id === params[0] && !k.revoked_at) as unknown as T[];
          if (q.startsWith("SELECT id FROM api_keys WHERE site_id=? AND revoked_at IS NULL"))
            return t.api_keys.filter((k) => k.site_id === params[0] && !k.revoked_at) as unknown as T[];
          if (q.startsWith("SELECT * FROM api_keys WHERE id=?"))
            return t.api_keys.filter((k) => k.id === params[0]) as unknown as T[];
          if (q.startsWith("SELECT site_id FROM api_keys WHERE key_hash=? AND revoked_at IS NULL")) {
            const k = t.api_keys.find((r) => r.key_hash === params[0] && !r.revoked_at);
            return (k ? [{ site_id: k.site_id }] : []) as unknown as T[];
          }
          if (q.startsWith("SELECT id,user_id,site_id,label,created_at,revoked_at FROM api_keys WHERE user_id=?")) {
            return t.api_keys
              .filter((k) => k.user_id === params[0])
              .map(stripHash) as unknown as T[];
          }
          if (q.startsWith("SELECT id,user_id,site_id,label,created_at,revoked_at FROM api_keys ORDER BY")) {
            return t.api_keys.map(stripHash) as unknown as T[];
          }
          if (q.startsWith("SELECT site_id,station_id,packager_fp,seen_at FROM reporter_events WHERE site_id=?")) {
            const lim = Number(params[1] ?? 50);
            return t.reporter_events
              .filter((r) => r.site_id === params[0])
              .sort((a, b) => String(b.seen_at).localeCompare(String(a.seen_at)))
              .slice(0, lim) as unknown as T[];
          }
          if (q.startsWith("SELECT * FROM sites ORDER BY site_id"))
            return [...t.sites].sort((a, b) => String(a.site_id).localeCompare(String(b.site_id))) as unknown as T[];
          if (q.startsWith("SELECT site_id, COUNT(*) AS n FROM api_keys WHERE revoked_at IS NULL GROUP BY site_id")) {
            const m = new Map<string, number>();
            for (const k of t.api_keys) if (!k.revoked_at) m.set(String(k.site_id), (m.get(String(k.site_id)) ?? 0) + 1);
            return [...m].map(([site_id, n]) => ({ site_id, n })) as unknown as T[];
          }
          if (q.startsWith("SELECT site_id, COUNT(DISTINCT station_id")) {
            const m = new Map<string, Set<string>>();
            for (const r of t.reporter_events) {
              const k = `${r.station_id}|${r.packager_fp}`;
              if (!m.has(String(r.site_id))) m.set(String(r.site_id), new Set());
              m.get(String(r.site_id))!.add(k);
            }
            return [...m].map(([site_id, s]) => ({ site_id, n: s.size })) as unknown as T[];
          }
          if (q.startsWith("SELECT * FROM licences WHERE site_id=?"))
            return t.reporter_events.length >= 0
              ? (t.licences.filter((l) => l.site_id === params[0]) as unknown as T[])
              : [];
          if (q.startsWith("SELECT * FROM licences ORDER BY"))
            return [...t.licences] as unknown as T[];
          if (q.startsWith("SELECT * FROM site_licences ORDER BY"))
            return [...t.site_licences].sort((a, b) =>
              String(b.updated_at).localeCompare(String(a.updated_at))) as unknown as T[];
          throw new Error(`memory D1: unsupported query: ${q}`);
        };
        const runWrite = (): void => {
          if (q.startsWith("INSERT INTO users(")) {
            const [id, email, pass_hash, role, site_id, disabled, created_at] = params;
            if (t.users.some((u) => u.email === email)) throw new Error("UNIQUE constraint failed: users.email");
            t.users.push({ id, email, pass_hash, role, site_id, disabled, created_at });
            return;
          }
          if (q.startsWith("UPDATE users SET role=?")) {
            const [role, site_id, disabled, id] = params;
            const u = t.users.find((x) => x.id === id);
            if (u) Object.assign(u, { role, site_id, disabled });
            return;
          }
          if (q.startsWith("INSERT INTO sessions(")) {
            const [token, user_id, expires] = params;
            t.sessions.push({ token, user_id, expires });
            return;
          }
          if (q.startsWith("DELETE FROM sessions WHERE token=?")) {
            t.sessions = t.sessions.filter((s) => s.token !== params[0]);
            return;
          }
          if (q.startsWith("INSERT INTO api_keys(")) {
            const [id, user_id, site_id, key_hash, label, created_at, revoked_at] = params;
            t.api_keys.push({ id, user_id, site_id, key_hash, label, created_at, revoked_at });
            return;
          }
          if (q.startsWith("UPDATE api_keys SET revoked_at=? WHERE id=?")) {
            const k = t.api_keys.find((x) => x.id === params[1]);
            if (k) k.revoked_at = params[0];
            return;
          }
          if (q.startsWith("INSERT INTO sites(")) {
            const siteId = params[0] as string;
            let s = t.sites.find((x) => x.site_id === siteId);
            if (!s) {
              s = { site_id: siteId, last_push_at: null, last_reporter_fp: null, frozen: 0 };
              t.sites.push(s);
            }
            if (q.includes("DO UPDATE SET last_push_at=excluded.last_push_at")) s.last_push_at = params[1];
            else if (q.includes("DO UPDATE SET last_reporter_fp")) {
              s.last_reporter_fp = params[2];
              if (s.last_push_at == null) s.last_push_at = params[1];
            } else if (q.includes("DO UPDATE SET frozen")) s.frozen = params[3];
            return;
          }
          if (q.startsWith("INSERT INTO reporter_events(")) {
            const [site_id, station_id, packager_fp, seen_at] = params;
            t.reporter_events.push({ id: t.reporter_events.length + 1, site_id, station_id, packager_fp, seen_at });
            return;
          }
          if (q.startsWith("INSERT INTO licences(")) {
            const [id, site_id, customer, serial, edition, packager_fp, max_stations, issued, expires, max_version, issued_by, created_at] = params;
            t.licences.push({ id, site_id, customer, serial, edition, packager_fp, max_stations, issued, expires, max_version, issued_by, created_at });
            return;
          }
          if (q.startsWith("INSERT INTO site_licences(")) {
            const [site_id, edition, expires, days_left, state, app_version, packager_fp, updated_at] = params;
            const row = { site_id, edition, expires, days_left, state, app_version, packager_fp, updated_at };
            const i = t.site_licences.findIndex((r) => r.site_id === site_id);
            if (i >= 0) t.site_licences[i] = row;
            else t.site_licences.push(row);
            return;
          }
          throw new Error(`memory D1: unsupported write: ${q}`);
        };
        return { first, all, run };
      },
    };
  }
}

function stripHash(k: Row): Row {
  const { key_hash: _h, ...rest } = k;
  void _h;
  return rest;
}

let memAccounts: MemoryAccountsStore | null = null;

/** D1 when the DB binding exists, memory fallback otherwise (dev only). */
export function accountsForEnv(env: Record<string, unknown>): AccountsStore {
  const db = env.DB as D1Like | undefined;
  if (db && typeof (db as D1Like).prepare === "function") return new D1AccountsStore(db);
  if (!memAccounts) memAccounts = new MemoryAccountsStore();
  return memAccounts;
}
