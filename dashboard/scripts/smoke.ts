// Dashboard smoke suite: login, RBAC, issuance round-trip, push-key revoke,
// releases manifest. Run: `npm run smoke` (node --test, no extra deps).
// Exercises the REAL modules (licence format, password, D1 SQL via node:sqlite,
// Worker routing with a fake env). TEST keypair below is throwaway, test-only.

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { DatabaseSync } from "node:sqlite";

import {
  canonicalString,
  issueLicence,
  licenceStatus,
  verifyLicence,
  verifySignature,
} from "../src/lib/license";
import { hashPassword, sha256Hex, verifyPassword } from "../src/lib/password";
import { accountsForEnv, initSchema } from "../src/lib/db";
import type { D1Like } from "../src/lib/db";
import worker from "../src/worker";

// Throwaway TEST-ONLY keypair (generated 2026-10-26, never a real vendor key).
const TEST_PRIV_B64 = "8rPxEvBWIUwXxI09a56QGm9czbsiWCY8kmxYrkw2BFk=";
const TEST_PUB_B64 = "a4lCVmW38ImlstT2gMyHs0LNEtdBOnNcZUE73fCPRSw=";

// Python-issued fixture (tools/issue_license.py) with a non-ASCII customer —
// pins canonical JSON parity incl. ensure_ascii escaping.
const PY_LICENCE = {
  customer: "Tëst Café ✓",
  edition: "pro",
  expires: "2027-10-26",
  features: ["lan", "history", "summary"],
  issued: "2026-10-26",
  max_stations: 3,
  max_version: "2.x",
  packager_fp: "abc123",
  serial: "TEST-001",
  site_id: "test-site",
  sig: "Vkio8BKvD/LeFAB/ZS/xaBWJqjdfW8Aq/JAjv3+i4lakNaldP1MZKInhASoRuhwPHkJ9wFGkTm53hnsdSISoDA==",
};
const PY_CANONICAL =
  '{"customer":"T\\u00ebst Caf\\u00e9 \\u2713","edition":"pro","expires":"2027-10-26","features":["lan","history","summary"],"issued":"2026-10-26","max_stations":3,"max_version":"2.x","packager_fp":"abc123","serial":"TEST-001","site_id":"test-site"}';

// ---------- licence format ----------

test("licence: canonical JSON matches Python byte-for-byte", () => {
  const { sig: _s, ...body } = PY_LICENCE;
  void _s;
  assert.equal(canonicalString(body), PY_CANONICAL);
});

test("licence: Python-signed fixture verifies with TEST pubkey", async () => {
  assert.equal(await verifySignature(PY_LICENCE, TEST_PUB_B64), true);
  assert.equal(await verifySignature({ ...PY_LICENCE, max_stations: 9 }, TEST_PUB_B64), false);
  assert.equal(await verifySignature({ ...PY_LICENCE, sig: "AAAA" }, TEST_PUB_B64), false);
});

test("licence: TS issuance round-trips (sign -> verify)", async () => {
  const lic = await issueLicence(
    { customer: "Acme", site_id: "s1", edition: "pro", packager_fp: "fp1", expires: "2027-01-01" },
    TEST_PRIV_B64
  );
  assert.ok(/^SIMPLD-\d{8}-001$/.test(lic.serial), `serial default shape: ${lic.serial}`);
  assert.deepEqual(lic.features, ["lan", "history", "summary"]);
  assert.equal(await verifySignature(lic, TEST_PUB_B64), true);
  const v = await verifyLicence(lic, { pubkeyB64: TEST_PUB_B64, machineFp: "fp1", appVersion: "2.0.1" });
  assert.deepEqual(v, { ok: true, reason: "ok" });
});

test("licence: Python cross-verifies a TS-issued licence (live)", async () => {
  const lic = await issueLicence(
    { customer: "X", site_id: "s9", edition: "simple", packager_fp: "fp9", expires: "2027-06-01" },
    TEST_PRIV_B64
  );
  assert.equal(lic.max_stations, 1); // simple clamps to 1
  let out = "";
  try {
    out = execFileSync(
      "python3",
      ["-c", "import json,sys; sys.path.insert(0,'factory'); from factory import license as L; print(L.verify_signature(json.load(sys.stdin), sys.argv[1]))", TEST_PUB_B64],
      { input: JSON.stringify(lic), cwd: "/home/onlybond/Desktop/Desktop/projects/simpld", encoding: "utf8" }
    );
  } catch {
    console.log("  (skip: python3/factory unavailable)");
    return;
  }
  assert.match(out, /True/);
});

test("licence: verifyLicence semantics mirror factory", async () => {
  const lic = await issueLicence(
    { customer: "X", site_id: "s1", edition: "pro", packager_fp: "fp1", expires: "2027-01-01" },
    TEST_PRIV_B64
  );
  const bad = { ...lic };
  delete (bad as Record<string, unknown>).serial;
  assert.match((await verifyLicence(bad, { pubkeyB64: TEST_PUB_B64 })).reason, /serial/);
  assert.match((await verifyLicence({ ...lic, edition: "nope" }, { pubkeyB64: TEST_PUB_B64 })).reason, /edition/);
  assert.match(
    (await verifyLicence(lic, { pubkeyB64: TEST_PUB_B64, machineFp: "other" })).reason,
    /another computer/
  );
  assert.match(
    (await verifyLicence(lic, { pubkeyB64: TEST_PUB_B64, appVersion: "3.0.0" })).reason,
    /covers v2/
  );
  assert.equal((await licenceStatus(lic, { pubkeyB64: TEST_PUB_B64, today: "2026-10-26" })).state, "ok");
  assert.equal((await licenceStatus(lic, { pubkeyB64: TEST_PUB_B64, today: "2026-12-25" })).state, "expiring");
  assert.equal((await licenceStatus(lic, { pubkeyB64: TEST_PUB_B64, today: "2027-01-10" })).state, "grace");
  assert.equal((await licenceStatus(lic, { pubkeyB64: TEST_PUB_B64, today: "2028-01-01" })).state, "blocked");
});

// ---------- password ----------

test("password: PBKDF2 hash/verify", async () => {
  const h = await hashPassword("correct horse 9");
  assert.match(h, /^pbkdf2-sha256\$100000\$/);
  assert.equal(await verifyPassword("correct horse 9", h), true);
  assert.equal(await verifyPassword("wrong", h), false);
  assert.equal(await verifyPassword("x", "garbage"), false);
  assert.equal(await sha256Hex("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
});

// ---------- worker: sqlite-backed env ----------

function sqliteD1(): D1Like {
  const db = new DatabaseSync(":memory:");
  return {
    prepare(query: string) {
      const stmt = db.prepare(query);
      return {
        bind(...params: unknown[]) {
          const args = params.map((p) => (p === undefined ? null : p));
          return {
            first: async <T>() => ((stmt.get(...args) as T | undefined) ?? null),
            all: async <T>() => ({ results: stmt.all(...args) as T[] }),
            run: async () => {
              stmt.run(...args);
              return { success: true };
            },
          };
        },
      };
    },
    exec: async (q: string) => {
      db.exec(q);
      return { success: true };
    },
  };
}

function memoryR2() {
  const map = new Map<string, Uint8Array>();
  return {
    map,
    async get(key: string) {
      const b = map.get(key);
      if (!b) return null;
      return { async arrayBuffer() { return b.buffer as ArrayBuffer; } };
    },
    async put(key: string, v: Uint8Array | ArrayBuffer) {
      map.set(key, v instanceof Uint8Array ? v : new Uint8Array(v));
    },
  };
}

const ADMIN_SEED_TOKEN = "test-seed-token-xyz";

function testEnv(r2?: ReturnType<typeof memoryR2>) {
  return {
    DB: sqliteD1(),
    SITE_KEYS: undefined,
    LICENSE_PRIVKEY_B64: TEST_PRIV_B64,
    ADMIN_SEED_TOKEN,
    RELEASES: r2 ?? null,
  } as unknown as Record<string, string | undefined> & { DB: unknown; RELEASES: unknown };
}

function req(path: string, init?: RequestInit & { cookie?: string }): Request {
  const headers = new Headers(init?.headers);
  if (init?.cookie) headers.set("cookie", init.cookie);
  return new Request(`http://x${path}`, { ...init, headers });
}

function sessionCookie(res: Response): string {
  const set = res.headers.get("set-cookie") ?? "";
  const m = /simpld_session=([0-9a-f]+)/.exec(set);
  assert.ok(m, `expected session cookie, got: ${set}`);
  return `simpld_session=${m[1]}`;
}

const dayBody = (site: string, extra?: Record<string, unknown>) => ({
  site_id: site,
  day_report: {
    day: "2026-10-26",
    reel_rows: [],
    box_rows: [],
    totals: { reel_count: 0, reel_net_kg: 0, box_count: 0, box_gross_kg: 0 },
    by_job: {},
  },
  ...extra,
});

test("portal: seed-once, login, me, logout", async () => {
  const env = testEnv();
  await initSchema(env.DB as D1Like);
  assert.equal((await accountsForEnv(env).userCount()), 0);

  let r = await worker.fetch(req("/api/admin/seed", { method: "POST", body: JSON.stringify({ token: "nope", email: "a@x.y", password: "password1" }) }), env);
  assert.equal(r.status, 403);

  r = await worker.fetch(
    req("/api/admin/seed", { method: "POST", body: JSON.stringify({ token: ADMIN_SEED_TOKEN, email: "admin@x.y", password: "password1" }) }),
    env
  );
  assert.equal(r.status, 200);
  const adminCookie = sessionCookie(r);

  r = await worker.fetch(req("/api/admin/seed", { method: "POST", body: JSON.stringify({ token: ADMIN_SEED_TOKEN, email: "b@x.y", password: "password22" }) }), env);
  assert.equal(r.status, 403); // already seeded

  r = await worker.fetch(req("/api/auth/me", { cookie: adminCookie }), env);
  assert.equal(r.status, 200);
  assert.equal((await r.json()).user.role, "vendor_admin");

  r = await worker.fetch(req("/api/auth/login", { method: "POST", body: JSON.stringify({ email: "admin@x.y", password: "wrongpass1" }) }), env);
  assert.equal(r.status, 401);

  r = await worker.fetch(req("/api/auth/logout", { method: "POST", cookie: adminCookie }), env);
  assert.equal(r.status, 200);
  r = await worker.fetch(req("/api/auth/me", { cookie: adminCookie }), env);
  assert.equal(r.status, 401);
});

test("portal: RBAC — client isolation + admin gates", async () => {
  const env = testEnv();
  await initSchema(env.DB as D1Like);
  const accounts = accountsForEnv(env);
  const admin = await accounts.createUser("root@v.y", "password1", "vendor_admin", null);
  const { token: adminTok } = await accounts.createSession(admin.id);
  const adminCookie = `simpld_session=${adminTok}`;

  // create two clients on different sites
  let r = await worker.fetch(
    req("/api/admin/users", { method: "POST", cookie: adminCookie, body: JSON.stringify({ email: "c1@site-a.x", password: "password11", role: "client", site_id: "site-a" }) }),
    env
  );
  assert.equal(r.status, 201);
  const c1 = (await r.json()).user;
  r = await worker.fetch(
    req("/api/admin/users", { method: "POST", cookie: adminCookie, body: JSON.stringify({ email: "c2@site-b.x", password: "password22", role: "client", site_id: "site-b" }) }),
    env
  );
  assert.equal(r.status, 201);

  // client without site_id rejected
  r = await worker.fetch(
    req("/api/admin/users", { method: "POST", cookie: adminCookie, body: JSON.stringify({ email: "c3@site-c.x", password: "password33", role: "client" }) }),
    env
  );
  assert.equal(r.status, 400);

  // client login
  r = await worker.fetch(req("/api/auth/login", { method: "POST", body: JSON.stringify({ email: "c1@site-a.x", password: "password11" }) }), env);
  assert.equal(r.status, 200);
  const c1cookie = sessionCookie(r);

  // client cannot touch admin API
  r = await worker.fetch(req("/api/admin/users", { cookie: c1cookie }), env);
  assert.equal(r.status, 403);
  r = await worker.fetch(req("/api/admin/sites", { cookie: c1cookie }), env);
  assert.equal(r.status, 403);

  // client licence issuance: own site ok, cross-site 403
  r = await worker.fetch(
    req("/api/licenses/issue", { method: "POST", cookie: c1cookie, body: JSON.stringify({ site_id: "site-b", customer: "X", packager_fp: "fp", expires: "2027-01-01" }) }),
    env
  );
  assert.equal(r.status, 403);
  r = await worker.fetch(
    req("/api/licenses/issue", { method: "POST", cookie: c1cookie, body: JSON.stringify({ site_id: "site-a", customer: "Acme", packager_fp: "fp-a", expires: "2027-01-01" }) }),
    env
  );
  assert.equal(r.status, 201);
  const lic = (await r.json()).licence;
  assert.equal(lic.site_id, "site-a");
  assert.equal(await verifySignature(lic, TEST_PUB_B64), true);

  // issuance log: client sees own site only
  r = await worker.fetch(req("/api/licenses", { cookie: c1cookie }), env);
  assert.equal((await r.json()).licences.length, 1);
  r = await worker.fetch(req("/api/licenses", { cookie: adminCookie }), env);
  assert.equal((await r.json()).licences.length, 1);

  // disabled user cannot log in
  await accounts.updateUser(c1.id, { disabled: true });
  r = await worker.fetch(req("/api/auth/login", { method: "POST", body: JSON.stringify({ email: "c1@site-a.x", password: "password11" }) }), env);
  assert.equal(r.status, 401);
});

test("portal: issuance derives tiers, renew works, overview gates", async () => {
  const env = testEnv();
  await initSchema(env.DB as D1Like);
  const accounts = accountsForEnv(env);
  const admin = await accounts.createUser("root@v.y", "password1", "vendor_admin", null);
  const { token: adminTok } = await accounts.createSession(admin.id);
  const adminCookie = `simpld_session=${adminTok}`;

  // forged tier values are ignored: simple always gets 1 station, no dates needed
  let r = await worker.fetch(
    req("/api/licenses/issue", {
      method: "POST",
      cookie: adminCookie,
      body: JSON.stringify({ site_id: "site-a", customer: "Acme", packager_fp: "fp-a", edition: "simple", max_stations: 99, expires: "2030-01-01", features: ["lan", "api"] }),
    }),
    env
  );
  assert.equal(r.status, 201);
  let lic = (await r.json()).licence;
  assert.equal(lic.max_stations, 1);
  assert.deepEqual(lic.features, []);
  assert.match(lic.issued, /^\d{4}-\d{2}-\d{2}$/);
  const dayAfterYear = new Date(lic.issued + "T00:00:00Z");
  dayAfterYear.setUTCDate(dayAfterYear.getUTCDate() + 365);
  assert.equal(lic.expires, dayAfterYear.toISOString().slice(0, 10));

  // pro derives 3 stations
  r = await worker.fetch(
    req("/api/licenses/issue", {
      method: "POST",
      cookie: adminCookie,
      body: JSON.stringify({ site_id: "site-a", customer: "Acme", packager_fp: "fp-a", edition: "pro" }),
    }),
    env
  );
  assert.equal(r.status, 201);
  lic = (await r.json()).licence;
  assert.equal(lic.max_stations, 3);
  assert.deepEqual(lic.features, ["lan", "history", "summary"]);
  assert.equal(await verifySignature(lic, TEST_PUB_B64), true);

  // renew mints a fresh serial with fresh dates
  r = await worker.fetch(
    req("/api/licenses/renew", { method: "POST", cookie: adminCookie, body: JSON.stringify({ serial: lic.serial }) }),
    env
  );
  assert.equal(r.status, 201);
  const renewed = (await r.json()).licence;
  assert.notEqual(renewed.serial, lic.serial);
  assert.equal(renewed.site_id, "site-a");
  assert.equal(renewed.packager_fp, "fp-a");

  // unknown serial 404s
  r = await worker.fetch(
    req("/api/licenses/renew", { method: "POST", cookie: adminCookie, body: JSON.stringify({ serial: "nope" }) }),
    env
  );
  assert.equal(r.status, 404);

  // overview: admin 200 with counts, client 403
  r = await worker.fetch(req("/api/admin/overview", { cookie: adminCookie }), env);
  assert.equal(r.status, 200);
  const ov = (await r.json()).overview;
  assert.equal(ov.licences_total, 3);
  assert.ok(ov.sites_total >= 0);

  const client = await accounts.createUser("c@site-a.x", "password11", "client", "site-a");
  const { token: cTok } = await accounts.createSession(client.id);
  r = await worker.fetch(req("/api/admin/overview", { cookie: `simpld_session=${cTok}` }), env);
  assert.equal(r.status, 403);

  // client renews own site ok, other site 404s
  r = await worker.fetch(
    req("/api/licenses/renew", { method: "POST", cookie: `simpld_session=${cTok}`, body: JSON.stringify({ serial: lic.serial }) }),
    env
  );
  assert.equal(r.status, 201);
  r = await worker.fetch(
    req("/api/licenses/renew", { method: "POST", cookie: `simpld_session=${cTok}`, body: JSON.stringify({ serial: "SIMPLD-unknown" }) }),
    env
  );
  assert.equal(r.status, 404);
});
  const env = testEnv();
  await initSchema(env.DB as D1Like);
  const accounts = accountsForEnv(env);
  const admin = await accounts.createUser("root@v.y", "password1", "vendor_admin", null);
  const { token: adminTok } = await accounts.createSession(admin.id);
  const adminCookie = `simpld_session=${adminTok}`;
  const client = await accounts.createUser("c@site-a.x", "password11", "client", "site-a");

  const push = (key: string, site: string, extra?: Record<string, unknown>) =>
    worker.fetch(
      req("/api/push-day", {
        method: "POST",
        headers: { Authorization: `Bearer ${key}` },
        body: JSON.stringify(dayBody(site, extra)),
      }),
      env
    );

  // no key -> 401
  let r = await worker.fetch(req("/api/push-day", { method: "POST", body: JSON.stringify(dayBody("site-a")) }), env);
  assert.equal(r.status, 401);

  // env fallback (dev-key -> demo-site) still works when D1 has no keys
  r = await push("dev-key", "demo-site");
  assert.equal(r.status, 200);

  // issue a D1 key for site-a
  r = await worker.fetch(
    req("/api/admin/keys", { method: "POST", cookie: adminCookie, body: JSON.stringify({ user_id: client.id, site_id: "site-a", label: "packager", confirm: true }) }),
    env
  );
  assert.equal(r.status, 201);
  const { key: k1 } = await r.json();

  // key without confirm -> 400
  r = await worker.fetch(
    req("/api/admin/keys", { method: "POST", cookie: adminCookie, body: JSON.stringify({ user_id: client.id, site_id: "site-a" }) }),
    env
  );
  assert.equal(r.status, 400);

  r = await push(k1, "site-a");
  assert.equal(r.status, 200);
  r = await push(k1, "site-b");
  assert.equal(r.status, 403); // cross-site

  // second key revokes the first (one active key per user)
  r = await worker.fetch(
    req("/api/admin/keys", { method: "POST", cookie: adminCookie, body: JSON.stringify({ user_id: client.id, site_id: "site-a", label: "rotated", confirm: true }) }),
    env
  );
  assert.equal(r.status, 201);
  const { key: k2, revoked_previous } = await r.json();
  assert.equal(revoked_previous, 1);
  r = await push(k1, "site-a");
  assert.equal(r.status, 401); // old key dead
  r = await push(k2, "site-a");
  assert.equal(r.status, 200);

  // explicit revoke -> 401
  const keys = (await (await worker.fetch(req("/api/admin/keys", { cookie: adminCookie }), env)).json()).keys;
  const active = keys.find((k: { revoked_at: string | null }) => !k.revoked_at);
  r = await worker.fetch(req(`/api/admin/keys/${active.id}/revoke`, { method: "POST", cookie: adminCookie, body: "{}" }), env);
  assert.equal(r.status, 200);
  r = await push(k2, "site-a");
  assert.equal(r.status, 401);

  // viewer: session auth (client own site ok after fresh key+push)
  r = await worker.fetch(
    req("/api/admin/keys", { method: "POST", cookie: adminCookie, body: JSON.stringify({ user_id: client.id, site_id: "site-a", label: "v", confirm: true }) }),
    env
  );
  const k3 = (await r.json()).key;
  await push(k3, "site-a");
  const cLogin = await worker.fetch(req("/api/auth/login", { method: "POST", body: JSON.stringify({ email: "c@site-a.x", password: "password11" }) }), env);
  const cCookie = sessionCookie(cLogin);
  r = await worker.fetch(req("/api/s/site-a", { cookie: cCookie }), env);
  assert.equal(r.status, 200);
  r = await worker.fetch(req("/api/s/site-b", { cookie: cCookie }), env);
  assert.equal(r.status, 403);
  r = await worker.fetch(req("/api/s/site-a", { cookie: adminCookie }), env);
  assert.equal(r.status, 200);
});

test("push: reporter extension recorded; old shapes still pass", async () => {
  const env = testEnv();
  await initSchema(env.DB as D1Like);
  const accounts = accountsForEnv(env);
  const admin = await accounts.createUser("root@v.y", "password1", "vendor_admin", null);
  const { token: adminTok } = await accounts.createSession(admin.id);
  const adminCookie = `simpld_session=${adminTok}`;
  const client = await accounts.createUser("c@site-a.x", "password11", "client", "site-a");
  const created = await accounts.createApiKey(client.id, "site-a", "p");
  const key = created.plaintext;

  const push = (body: unknown) =>
    worker.fetch(req("/api/push-day", { method: "POST", headers: { Authorization: `Bearer ${key}` }, body: JSON.stringify(body) }), env);

  let r = await push(dayBody("site-a"));
  assert.equal(r.status, 200);
  r = await push(dayBody("site-a", { reporter: { station_id: "st1", packager_fp: "fp-a" }, stations: [{ station_id: "st2", packager_fp: "fp-a" }] }));
  assert.equal(r.status, 200);
  r = await push(dayBody("site-a", { reporter: "nonsense", stations: [42], unknown_future_field: [1, 2] }));
  assert.equal(r.status, 200); // ignored, not tightened

  r = await worker.fetch(req("/api/admin/sites", { cookie: adminCookie }), env);
  const sites = (await r.json()).sites as Array<{ site_id: string; last_reporter_fp: string | null; reporters_seen: number }>;
  const s = sites.find((x) => x.site_id === "site-a");
  assert.ok(s);
  assert.equal(s.reporters_seen, 2);
  assert.equal(s.last_reporter_fp, "fp-a");

  r = await worker.fetch(req("/api/admin/sites/site-a/reporters", { cookie: adminCookie }), env);
  assert.equal(((await r.json()).reporters as unknown[]).length, 2);

  // freeze revokes keys (viewer + pushes 401)
  r = await worker.fetch(req("/api/admin/sites/site-a/freeze", { method: "POST", cookie: adminCookie, body: "{}" }), env);
  assert.equal(r.status, 200);
  assert.equal((await r.json()).revoked_keys, 1);
  r = await push(dayBody("site-a"));
  assert.equal(r.status, 401);
});

test("releases: 501 without R2; upload/list/download + downgrade guard with R2", async () => {
  const noR2 = testEnv();
  await initSchema(noR2.DB as D1Like);
  const accounts = accountsForEnv(noR2);
  const admin = await accounts.createUser("root@v.y", "password1", "vendor_admin", null);
  const { token } = await accounts.createSession(admin.id);
  const cookie = `simpld_session=${token}`;
  let r = await worker.fetch(req("/api/releases", { cookie }), noR2);
  assert.equal(r.status, 501);

  const r2 = memoryR2();
  const env = testEnv(r2);
  await initSchema(env.DB as D1Like);
  const acc2 = accountsForEnv(env);
  const a2 = await acc2.createUser("root@v.y", "password1", "vendor_admin", null);
  const s2 = await acc2.createSession(a2.id);
  const c2 = `simpld_session=${s2.token}`;

  const upload = (name: string, bytes: Uint8Array, notes: string, force?: boolean) => {
    const f = new FormData();
    f.set("file", new File([bytes as unknown as BlobPart], name, { type: "application/octet-stream" }));
    f.set("notes", notes);
    if (force) f.set("force", "1");
    return worker.fetch(new Request("http://x/api/admin/releases", { method: "POST", headers: { cookie: c2 }, body: f }), env);
  };

  r = await upload("wrong-name.exe", new TextEncoder().encode("x"), "n");
  assert.equal(r.status, 400);
  r = await upload("Simpld-Setup-1.0.1.exe", new TextEncoder().encode("fake-installer-bytes"), "first");
  assert.equal(r.status, 201);
  const rel1 = (await r.json()).release;
  assert.equal(rel1.version, "1.0.1");
  assert.equal(rel1.sha256.length, 64);

  r = await worker.fetch(req("/api/releases", { cookie: c2 }), env);
  assert.equal(r.status, 200);
  const list = await r.json();
  assert.equal(list.latest.version, "1.0.1");
  assert.equal(list.releases.length, 1);

  r = await worker.fetch(req("/api/releases/file/Simpld-Setup-1.0.1.exe", { cookie: c2 }), env);
  assert.equal(r.status, 200);
  assert.equal(await r.text(), "fake-installer-bytes");

  // downgrade refused, force allowed
  r = await upload("Simpld-Setup-1.0.0.exe", new TextEncoder().encode("old"), "old");
  assert.equal(r.status, 409);
  r = await upload("Simpld-Setup-1.0.0.exe", new TextEncoder().encode("old"), "old", true);
  assert.equal(r.status, 201);

  // logged-out download denied
  r = await worker.fetch(req("/api/releases/file/Simpld-Setup-1.0.1.exe"), env);
  assert.equal(r.status, 401);

  // latest.json shape matches UPDATES.md
  const latestRaw = r2.map.get("releases/latest.json");
  assert.ok(latestRaw);
  const latest = JSON.parse(new TextDecoder().decode(latestRaw));
  assert.deepEqual(Object.keys(latest).sort(), ["file", "notes", "sha256", "version"]);
});
