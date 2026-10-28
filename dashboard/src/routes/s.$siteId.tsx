import { useEffect, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { HugeiconsIcon } from "@hugeicons/react";
import {
  PackageIcon,
  BoxesIcon,
  DeliveryTruck01Icon,
  Chart01Icon,
  Clock01Icon,
} from "@hugeicons/core-free-icons";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { SiteSnapshot } from "@/lib/contract";

// Viewer: GET /s/<site_id> read-only — yesterday + today production
// (day_report payload), order loaded/target, stock counts. Buyer-scoped note.
export const Route = createFileRoute("/s/$siteId")({
  validateSearch: (s: Record<string, unknown>) => ({ key: (s.key as string) ?? "" }),
  component: SiteViewerRoute,
});

function SiteViewerRoute() {
  const { siteId } = Route.useParams();
  const { key } = Route.useSearch();
  return <SiteViewer siteId={siteId} viewerKey={key} />;
}

function mockSnapshot(siteId: string): SiteSnapshot {
  const today = new Date().toISOString().slice(0, 10);
  const yd = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const mk = (day: string) => ({
    day,
    reel_rows: [],
    box_rows: [],
    totals: { reel_count: 0, reel_net_kg: 0, box_count: 0, box_gross_kg: 0 },
    by_job: {},
  });
  return {
    site_id: siteId,
    pushed_at: new Date().toISOString(),
    days: { [yd]: mk(yd), [today]: mk(today) },
    orders: [],
    stock_counts: { raw_rolls: 0, packed_cartons: 0, dispatched_cartons: 0 },
  };
}

function Stat({
  icon,
  label,
  value,
  sub,
}: {
  icon: typeof PackageIcon;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{label}</CardTitle>
        <HugeiconsIcon icon={icon} size={18} className="text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
      </CardContent>
    </Card>
  );
}

export function SiteViewer({ siteId, viewerKey }: { siteId: string; viewerKey: string }) {
  const [snap, setSnap] = useState<SiteSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mock, setMock] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // Key-only viewers pass ?key=; logged-in users rely on their session
      // cookie — an empty Bearer header would force the key path server-side
      // and 401 instead of falling back to the session.
      const headers: Record<string, string> = {};
      if (viewerKey) headers.Authorization = `Bearer ${viewerKey}`;
      let res: Response;
      try {
        res = await fetch(`/api/s/${encodeURIComponent(siteId)}`, { headers });
      } catch {
        // Dev fallback: no Worker backend under `vite dev` — show mock shape.
        if (!cancelled) {
          setSnap(mockSnapshot(siteId));
          setMock(true);
          setError(null);
        }
        return;
      }
      if (res.status === 403) {
        if (!cancelled) setError("This login cannot view this site.");
        return;
      }
      if (res.status === 401) {
        if (!cancelled) setError("Invalid or revoked key — ask your vendor admin.");
        return;
      }
      if (res.status === 404) {
        if (!cancelled) setError("No data pushed for this site yet — sync from the packager first.");
        return;
      }
      if (!res.ok) {
        if (!cancelled) setError(`Viewer request failed (${res.status}).`);
        return;
      }
      const data = (await res.json()) as SiteSnapshot;
      if (!cancelled) setSnap(data);
    })();
    return () => {
      cancelled = true;
    };
  }, [siteId, viewerKey]);

  if (error)
    return (
      <Card className="border-destructive">
        <CardHeader>
          <CardTitle className="text-base">Cannot open viewer</CardTitle>
          <CardDescription>{error}</CardDescription>
        </CardHeader>
      </Card>
    );
  if (!snap)
    return (
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-28" />
        ))}
      </div>
    );

  const days = Object.keys(snap.days).sort().slice(-2); // yesterday + today
  const latest = days.length ? snap.days[days[days.length - 1]] : null;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-2xl font-bold tracking-tight">{snap.site_id}</h1>
        <Badge variant="secondary">buyer-scoped · read-only</Badge>
        {mock && <Badge variant="outline">dev mock data</Badge>}
      </div>
      <p className="-mt-4 text-sm text-muted-foreground">
        Last push {snap.pushed_at} · no factory access from here.
      </p>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          icon={PackageIcon}
          label="Reels today"
          value={latest ? String(latest.totals.reel_count) : "—"}
          sub={latest ? `${latest.totals.reel_net_kg.toFixed(1)} kg net` : undefined}
        />
        <Stat
          icon={BoxesIcon}
          label="Boxes today"
          value={latest ? String(latest.totals.box_count) : "—"}
          sub={latest ? `${latest.totals.box_gross_kg.toFixed(1)} kg gross` : undefined}
        />
        <Stat
          icon={Chart01Icon}
          label="Orders in progress"
          value={String(snap.orders.filter((o) => o.status !== "complete").length)}
          sub={`${snap.orders.length} total pushed`}
        />
        <Stat
          icon={Clock01Icon}
          label="Ready stock"
          value={snap.stock_counts ? String(snap.stock_counts.packed_cartons) : "—"}
          sub={
            snap.stock_counts
              ? `${snap.stock_counts.raw_rolls} raw rolls · ${snap.stock_counts.dispatched_cartons} dispatched`
              : undefined
          }
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <HugeiconsIcon icon={DeliveryTruck01Icon} size={18} /> Orders — loaded / target
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Order</TableHead>
                <TableHead>Buyer</TableHead>
                <TableHead>Progress</TableHead>
                <TableHead className="text-right">Loaded / Target</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {snap.orders.map((o) => {
                const pct = o.target_qty > 0 ? Math.min(100, (o.loaded_qty / o.target_qty) * 100) : 0;
                return (
                  <TableRow key={o.order_id}>
                    <TableCell className="font-medium">{o.order_id}</TableCell>
                    <TableCell>{o.buyer}</TableCell>
                    <TableCell className="min-w-32">
                      <div className="h-2 overflow-hidden rounded-full bg-secondary">
                        <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      {o.loaded_qty} / {o.target_qty}
                    </TableCell>
                    <TableCell>
                      <Badge variant={o.status === "complete" ? "default" : "secondary"}>
                        {o.status}
                      </Badge>
                    </TableCell>
                  </TableRow>
                );
              })}
              {snap.orders.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="text-muted-foreground">
                    No orders pushed yet.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
