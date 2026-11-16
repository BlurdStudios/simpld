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
// Session only: log in as a client (own site) or vendor admin (any site).
export const Route = createFileRoute("/s/$siteId")({
  component: SiteViewerRoute,
});

function SiteViewerRoute() {
  const { siteId } = Route.useParams();
  return <SiteViewer siteId={siteId} />;
}

function mockSnapshot(siteId: string): SiteSnapshot {
  const days: Record<string, SiteSnapshot["days"][string]> = {};
  const mkReel = (i: number, job: string, net: number) => ({
    roll_code: `RC-${i.toString().padStart(4, "0")}`,
    roll_label: `Roll ${i}`,
    job_id: job,
    net_kg: net,
  });
  const mkBox = (i: number, job: string, gross: number, rolls: string[]) => ({
    carton_no: `CT-${i.toString().padStart(4, "0")}`,
    bundle_no: `B-${Math.ceil(i / 4)}`,
    job_id: job,
    num_reels: rolls.length,
    total_gross_kg: gross,
    linked_rolls: rolls,
  });
  const jobs = ["J-1042", "J-1043", "J-1044"];
  for (let d = 0; d < 14; d++) {
    const day = new Date(Date.now() - d * 86400000).toISOString().slice(0, 10);
    const reelRows = [];
    const boxRows = [];
    let reelNet = 0;
    let boxGross = 0;
    for (let i = 0; i < 8 + (d % 5); i++) {
      const job = jobs[i % jobs.length];
      const net = 12 + (i % 6) * 3 + d;
      reelRows.push(mkReel(d * 100 + i + 1, job, net));
      reelNet += net;
    }
    for (let i = 0; i < 3 + (d % 3); i++) {
      const job = jobs[i % jobs.length];
      const rolls = reelRows.slice(i * 2, i * 2 + 2).map((r) => r.roll_code);
      const gross = 24 + i * 7 + d;
      boxRows.push(mkBox(d * 100 + i + 1, job, gross, rolls));
      boxGross += gross;
    }
    const byJob: Record<string, { reels: number; reel_net_kg: number; boxes: number; box_gross_kg: number }> = {};
    for (const j of jobs) {
      const jr = reelRows.filter((r) => r.job_id === j);
      const jb = boxRows.filter((b) => b.job_id === j);
      byJob[j] = {
        reels: jr.length,
        reel_net_kg: jr.reduce((a, r) => a + r.net_kg, 0),
        boxes: jb.length,
        box_gross_kg: jb.reduce((a, b) => a + b.total_gross_kg, 0),
      };
    }
    days[day] = {
      day,
      reel_rows: reelRows,
      box_rows: boxRows,
      totals: {
        reel_count: reelRows.length,
        reel_net_kg: reelNet,
        box_count: boxRows.length,
        box_gross_kg: boxGross,
      },
      by_job: byJob,
    };
  }
  return {
    site_id: siteId,
    pushed_at: new Date().toISOString(),
    days,
    orders: [
      { order_id: "ORD-2041", buyer: "Acme Corp", target_qty: 5000, loaded_qty: 3820, status: "in_progress" },
      { order_id: "ORD-2042", buyer: "Globex", target_qty: 2000, loaded_qty: 2000, status: "complete" },
      { order_id: "ORD-2043", buyer: "Initech", target_qty: 3500, loaded_qty: 900, status: "in_progress" },
      { order_id: "ORD-2044", buyer: "Umbrella", target_qty: 1200, loaded_qty: 0, status: "pending" },
    ],
    stock_counts: { raw_rolls: 47, packed_cartons: 18, dispatched_cartons: 62 },
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

export function SiteViewer({ siteId }: { siteId: string }) {
  const [snap, setSnap] = useState<SiteSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [empty, setEmpty] = useState(false);
  const [mock, setMock] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // Session cookie auth only — no viewer keys.
      let res: Response;
      try {
        res = await fetch(`/api/s/${encodeURIComponent(siteId)}`);
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
        if (!cancelled) setError("Log in to view this site.");
        return;
      }
      if (res.status === 404) {
        if (!cancelled) setEmpty(true);
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
  }, [siteId]);

  if (empty)
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">No data yet</CardTitle>
          <CardDescription>Your figures will appear here after the first sync from the factory.</CardDescription>
        </CardHeader>
      </Card>
    );
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
