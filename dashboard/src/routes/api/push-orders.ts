// POST /api/push-orders  { site_id, orders, stock_counts, reporter?, stations? }
// Shared handler used by the Worker entry (and documented for factory/sync.py).
// Auth: Authorization: Bearer <API_KEY> or X-Site-Key header, bound to site_id.
// v2: optional `reporter` / `stations[]` persisted for the admin Sites view.

import { authorizeAsync, type AsyncKeyStore } from "../../lib/auth";
import { extractReporter, extractStations, validateOrdersPayload } from "../../lib/contract";
import type { OrderProgress, StockCounts } from "../../lib/contract";
import type { PushStore, ReporterLog } from "../../lib/store";

export async function handlePushOrders(
  req: Request,
  deps: { keys: AsyncKeyStore; store: PushStore; reporters: ReporterLog }
): Promise<Response> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "invalid json" }, { status: 400 });
  }
  const b = body as Record<string, unknown>;
  if (typeof b.site_id !== "string" || !b.site_id) {
    return Response.json({ error: "site_id required" }, { status: 400 });
  }
  const auth = await authorizeAsync(req, b.site_id, deps.keys);
  if (!auth.ok) return Response.json({ error: auth.error }, { status: auth.status });
  if (!validateOrdersPayload(body)) {
    return Response.json({ error: "invalid orders/stock_counts shape (see SYNC_CONTRACT.md v2)" }, { status: 400 });
  }
  const p = body as { orders: OrderProgress[]; stock_counts: StockCounts };
  const at = new Date().toISOString();
  await deps.store.saveOrders(auth.site_id, p.orders, p.stock_counts);
  await deps.reporters.touchPush(auth.site_id, at);
  const rep = extractReporter(body);
  if (rep) await deps.reporters.record(auth.site_id, { ...rep, seen_at: at });
  for (const s of extractStations(body)) {
    await deps.reporters.record(auth.site_id, { ...s, seen_at: at });
  }
  return Response.json({ ok: true, site_id: auth.site_id, orders: p.orders.length });
}
