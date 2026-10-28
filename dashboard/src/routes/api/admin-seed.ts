// POST /api/admin/seed  { token, email, password }
// One-time bootstrap: creates the first vendor_admin AND logs them in.
// Succeeds ONLY when the users table is empty AND token === ADMIN_SEED_TOKEN
// secret. After the first admin exists this endpoint always 403 — further
// accounts are created by an admin in the Admin panel.

import { initSchema, type AccountsStore, type D1Like } from "../../lib/db";
import { json, loginResponse } from "../../lib/portal";

export async function handleSeed(
  req: Request,
  env: Record<string, string | undefined> & { DB?: unknown },
  accounts: AccountsStore
): Promise<Response> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return json({ error: "invalid json" }, 400);
  }
  const b = body as Record<string, unknown>;
  const expect = (env.ADMIN_SEED_TOKEN ?? "").trim();
  if (!expect || typeof b.token !== "string" || b.token !== expect) {
    return json({ error: "forbidden" }, 403);
  }
  if (env.DB && typeof (env.DB as D1Like).exec === "function") {
    await initSchema(env.DB as D1Like);
  }
  if ((await accounts.userCount()) > 0) {
    return json({ error: "already seeded — ask an admin to create your account" }, 403);
  }
  const email = typeof b.email === "string" ? b.email : "";
  const password = typeof b.password === "string" ? b.password : "";
  if (!email || !password) return json({ error: "email and password required" }, 400);
  try {
    const user = await accounts.createUser(email, password, "vendor_admin", null);
    const { token } = await accounts.createSession(user.id);
    return loginResponse(user, token);
  } catch (e) {
    return json({ error: e instanceof Error ? e.message : "seed failed" }, 400);
  }
}
