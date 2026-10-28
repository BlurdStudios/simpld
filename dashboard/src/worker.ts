// Worker entry: API + viewer data endpoint + static SPA.
// Outbound-only design: the Worker NEVER calls into the factory. The factory
// POSTs cached payloads here; viewers only read what was pushed.
//
// Routes (see SYNC_CONTRACT.md v2):
//   POST /api/push-day, POST /api/push-orders (factory, key-bound to site_id;
//        keys resolve from the D1 api_keys table, SITE_KEYS env map fallback)
//   GET  /api/s/:siteId          (viewer data; same key auth, buyer-scoped —
//        or portal session: clients see their own site, admins any site)
//   POST /api/auth/login|logout, GET /api/auth/me
//   POST /api/admin/seed         (one-time bootstrap, token-gated)
//   /api/admin/users|keys|sites  (vendor_admin only)
//   POST /api/licenses/issue, GET /api/licenses (session; clients own site)
//   GET  /api/releases, GET /api/releases/file/:name (session)
//   POST /api/admin/releases     (vendor_admin only, multipart upload to R2)
//   GET  /s/:siteId, GET /       (SPA pages — served from ./dist assets)

import { accountsForEnv, initSchema, type AccountsStore, type D1Like } from "./lib/db";
import { authorizeAsync, canAccessSite, sessionTokenFrom } from "./lib/auth";
import { compositeKeyStore, getSessionUser, json, needAdmin, needAuth } from "./lib/portal";
import { reportersForEnv, storeForEnv } from "./lib/store";
import { handlePushDay } from "./routes/api/push-day";
import { handlePushOrders } from "./routes/api/push-orders";
import { handleLogin, handleLogout, handleMe } from "./routes/api/auth";
import { handleSeed } from "./routes/api/admin-seed";
import { handleAdminOverview } from "./routes/api/admin-overview";
import { handleAdminUsers } from "./routes/api/admin-users";
import { handleAdminKeys } from "./routes/api/admin-keys";
import { handleAdminSites } from "./routes/api/admin-sites";
import { handleIssueLicence, handleListLicences, handleRenewLicence } from "./routes/api/licenses";
import {
  handleDownloadRelease,
  handleListReleases,
  handleUploadRelease,
  type R2Like,
} from "./routes/api/releases";

type Env = Record<string, string | undefined> & {
  PUSHES?: unknown;
  DB?: unknown;
  RELEASES?: unknown;
  ASSETS?: { fetch(r: Request): Promise<Response> };
};

function r2ForEnv(env: Env): R2Like | null {
  const b = env.RELEASES as {
    get(k: string): Promise<{ arrayBuffer(): Promise<ArrayBuffer> } | null>;
    put(k: string, v: ArrayBuffer | Uint8Array): Promise<unknown>;
  } | null;
  if (!b || typeof b.get !== "function" || typeof b.put !== "function") return null;
  return {
    async get(key) {
      const o = await b.get(key);
      if (!o) return null;
      const buf = await o.arrayBuffer();
      return { body: new Uint8Array(buf), size: buf.byteLength };
    },
    async put(key, data) {
      await b.put(key, data instanceof Uint8Array ? data.buffer as ArrayBuffer : data);
    },
  };
}

let schemaReady: string | null = null;

async function ensureSchema(env: Env, accounts: AccountsStore): Promise<void> {
  const db = env.DB as D1Like | undefined;
  if (!db || typeof db.exec !== "function") return;
  if (schemaReady === "ok") return;
  try {
    await initSchema(db);
    schemaReady = "ok";
  } catch {
    // Migrations are authoritative (DEPLOY.md); this is best-effort for
    // the bootstrap path. A missing table surfaces as a 500 with the cause.
  }
  void accounts;
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    const path = url.pathname;
    const accounts = accountsForEnv(env as Record<string, unknown>);
    const keys = compositeKeyStore(env, accounts);

    if (req.method === "POST" && path === "/api/push-day") {
      const store = storeForEnv(env as Record<string, unknown>);
      const reporters = reportersForEnv(env as Record<string, unknown>, accounts);
      await ensureSchema(env, accounts);
      return handlePushDay(req, { keys, store, reporters });
    }
    if (req.method === "POST" && path === "/api/push-orders") {
      const store = storeForEnv(env as Record<string, unknown>);
      const reporters = reportersForEnv(env as Record<string, unknown>, accounts);
      await ensureSchema(env, accounts);
      return handlePushOrders(req, { keys, store, reporters });
    }

    if (req.method === "POST" && path === "/api/auth/login") {
      await ensureSchema(env, accounts);
      return handleLogin(req, accounts);
    }
    if (req.method === "POST" && path === "/api/auth/logout") return handleLogout(req, accounts);
    if (req.method === "GET" && path === "/api/auth/me") return handleMe(req, accounts);
    if (req.method === "POST" && path === "/api/admin/seed") return handleSeed(req, env, accounts);

    const viewer = /^\/api\/s\/([^/]+)$/.exec(path);
    if (req.method === "GET" && viewer) {
      const siteId = decodeURIComponent(viewer[1]);
      const store = storeForEnv(env as Record<string, unknown>);
      // Key auth first (factory + viewer keys, buyer-scoped).
      if (req.headers.get("authorization") || req.headers.get("x-site-key")) {
        const auth = await authorizeAsync(req, siteId, keys);
        if (!auth.ok) return json({ error: auth.error }, auth.status);
      } else if (sessionTokenFrom(req)) {
        const me = await getSessionUser(req, accounts);
        if (!me) return needAuth();
        if (!canAccessSite({ id: me.id, email: me.email, role: me.role, site_id: me.site_id }, siteId)) {
          return json({ error: "clients may only view their own site" }, 403);
        }
      } else {
        return json({ error: "missing api key" }, 401);
      }
      const snap = await store.loadSite(siteId);
      if (!snap) return json({ error: "no data pushed yet" }, 404);
      return json(snap);
    }

    // ---- session-authenticated portal API ----
    if (path === "/api/licenses/issue" && req.method === "POST") {
      const me = await getSessionUser(req, accounts);
      if (!me) return needAuth();
      await ensureSchema(env, accounts);
      return handleIssueLicence(req, env, accounts, me);
    }
    if (path === "/api/licenses" && req.method === "GET") {
      const me = await getSessionUser(req, accounts);
      if (!me) return needAuth();
      return handleListLicences(accounts, me);
    }
    if (path === "/api/licenses/renew" && req.method === "POST") {
      const me = await getSessionUser(req, accounts);
      if (!me) return needAuth();
      await ensureSchema(env, accounts);
      return handleRenewLicence(req, env, accounts, me);
    }
    if (path === "/api/releases" && req.method === "GET") {
      const me = await getSessionUser(req, accounts);
      if (!me) return needAuth();
      return handleListReleases(r2ForEnv(env));
    }
    const dl = /^\/api\/releases\/file\/([^/]+)$/.exec(path);
    if (req.method === "GET" && dl) {
      const me = await getSessionUser(req, accounts);
      if (!me) return needAuth();
      return handleDownloadRelease(r2ForEnv(env), decodeURIComponent(dl[1]));
    }

    // ---- vendor_admin API ----
    const admin = async (): Promise<null | Response> => {
      await ensureSchema(env, accounts);
      const me = await getSessionUser(req, accounts);
      if (!me) return needAuth();
      if (me.role !== "vendor_admin") return needAdmin();
      return null;
    };
    if (path === "/api/admin/overview" && req.method === "GET") {
      const denied = await admin();
      if (denied) return denied;
      return handleAdminOverview(accounts);
    }
    if (path === "/api/admin/users" || path.startsWith("/api/admin/users/")) {
      const denied = await admin();
      if (denied) return denied;
      return handleAdminUsers(req, accounts, path.slice("/api/admin/users".length));
    }
    if (path === "/api/admin/keys" || path.startsWith("/api/admin/keys/")) {
      const denied = await admin();
      if (denied) return denied;
      return handleAdminKeys(req, accounts, path.slice("/api/admin/keys".length), url.searchParams);
    }
    if (path === "/api/admin/sites" || path.startsWith("/api/admin/sites/")) {
      const denied = await admin();
      if (denied) return denied;
      return handleAdminSites(req, accounts, path.slice("/api/admin/sites".length));
    }
    if (path === "/api/admin/releases" && req.method === "POST") {
      const denied = await admin();
      if (denied) return denied;
      return handleUploadRelease(req, r2ForEnv(env));
    }

    // SPA static assets (landing + viewer + portal pages call the JSON API above).
    if (env.ASSETS) return env.ASSETS.fetch(req);
    return new Response("dashboard assets not built (run npm run build)", { status: 501 });
  },
};
