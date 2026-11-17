// Presentational pieces shared by the customer-portal pages. Real content and
// boneyard fixtures render the same components, so captured bones match.

import { useEffect, useState } from "react";
import { Navigate } from "@tanstack/react-router";
import { HugeiconsIcon } from "@hugeicons/react";
import {
  PackageIcon,
  BoxesIcon,
  DeliveryTruck01Icon,
  Chart01Icon,
  Download01Icon,
  File01Icon,
} from "@hugeicons/core-free-icons";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { DayReport, OrderProgress, ReleaseManifest, StockCounts } from "@/lib/contract";
import type { LicenceLogRow } from "@/lib/db";
import type { LoadState } from "@/lib/site-data";
import { Restricted } from "@/lib/session";

// ---------- shared cards ----------

export function Stat({
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

export function SiteHeading({
  siteId,
  title,
  description,
  pushedAt,
  mock,
}: {
  siteId: string;
  title?: string;
  description?: string;
  pushedAt?: string;
  mock?: boolean;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-2xl font-bold tracking-tight">{title ?? siteId}</h1>
        <Badge variant="secondary">buyer-scoped · read-only</Badge>
        {mock && <Badge variant="outline">dev mock data</Badge>}
      </div>
      <p className="text-sm text-muted-foreground">
        {description ?? (pushedAt ? `Last push ${pushedAt} · no factory access from here.` : `Site ${siteId} · no factory access from here.`)}
      </p>
    </div>
  );
}

/** Same empty/error Card style as the old viewer, reused on every page. */
export function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
    </Card>
  );
}

export function ErrorState({ title, description }: { title: string; description: string }) {
  return (
    <Card className="border-destructive">
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
    </Card>
  );
}

/** Shared gate for customer pages: 401 goes to this site's own login, 403 is
 *  restricted content, other failures keep the page's error card. */
export function SiteGate<T>({
  state,
  siteId,
  title = "Cannot open viewer",
}: {
  state: LoadState<T>;
  siteId: string;
  title?: string;
}) {
  if (state.status === "unauthenticated")
    return <Navigate to="/s/$siteId/login" params={{ siteId }} replace />;
  if (state.status === "forbidden") return <Restricted />;
  if (state.status === "error") return <ErrorState title={title} description={state.message} />;
  return null;
}

function TotalsStrip({ totals }: { totals: DayReport["totals"] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {[
        { label: "Reels", value: String(totals.reel_count) },
        { label: "Reel net", value: `${totals.reel_net_kg.toFixed(1)} kg` },
        { label: "Boxes", value: String(totals.box_count) },
        { label: "Box gross", value: `${totals.box_gross_kg.toFixed(1)} kg` },
      ].map((t) => (
        <div key={t.label} className="rounded-lg border bg-muted/30 p-3">
          <p className="text-xs text-muted-foreground">{t.label}</p>
          <p className="text-lg font-semibold">{t.value}</p>
        </div>
      ))}
    </div>
  );
}

function ByJobTable({ day }: { day: DayReport }) {
  const jobs = Object.keys(day.by_job ?? {}).sort();
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Job</TableHead>
          <TableHead className="text-right">Reels</TableHead>
          <TableHead className="text-right">Net kg</TableHead>
          <TableHead className="text-right">Boxes</TableHead>
          <TableHead className="text-right">Gross kg</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {jobs.map((job) => {
          const t = day.by_job[job];
          return (
            <TableRow key={job}>
              <TableCell className="font-medium">{job}</TableCell>
              <TableCell className="text-right">{t.reels}</TableCell>
              <TableCell className="text-right">{t.reel_net_kg.toFixed(1)}</TableCell>
              <TableCell className="text-right">{t.boxes}</TableCell>
              <TableCell className="text-right">{t.box_gross_kg.toFixed(1)}</TableCell>
            </TableRow>
          );
        })}
        {jobs.length === 0 && (
          <TableRow>
            <TableCell colSpan={5} className="text-muted-foreground">No job breakdown pushed.</TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  );
}

// ---------- Overview: compact latest-day summary (no big tables) ----------

export function LatestDaySummary({ day }: { day: DayReport }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <HugeiconsIcon icon={Chart01Icon} size={18} /> Latest day — {day.day}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <TotalsStrip totals={day.totals} />
        <div>
          <p className="mb-2 text-sm font-medium">By job</p>
          <ByJobTable day={day} />
        </div>
      </CardContent>
    </Card>
  );
}

// ---------- Production: day picker + detail tables ----------

function ReelRowsTable({ rows }: { rows: DayReport["reel_rows"] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Roll code</TableHead>
          <TableHead>Label</TableHead>
          <TableHead>Job</TableHead>
          <TableHead className="text-right">Net kg</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.roll_code}>
            <TableCell className="font-medium">{r.roll_code}</TableCell>
            <TableCell>{r.roll_label}</TableCell>
            <TableCell>{r.job_id}</TableCell>
            <TableCell className="text-right">{r.net_kg.toFixed(1)}</TableCell>
          </TableRow>
        ))}
        {rows.length === 0 && (
          <TableRow>
            <TableCell colSpan={4} className="text-muted-foreground">No reels pushed for this day.</TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  );
}

function BoxRowsTable({ rows }: { rows: DayReport["box_rows"] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Carton</TableHead>
          <TableHead>Bundle</TableHead>
          <TableHead>Job</TableHead>
          <TableHead className="text-right">Reels</TableHead>
          <TableHead className="text-right">Gross kg</TableHead>
          <TableHead>Linked rolls</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((b) => (
          <TableRow key={b.carton_no}>
            <TableCell className="font-medium">{b.carton_no}</TableCell>
            <TableCell>{b.bundle_no ?? "—"}</TableCell>
            <TableCell>{b.job_id}</TableCell>
            <TableCell className="text-right">{b.num_reels}</TableCell>
            <TableCell className="text-right">{b.total_gross_kg.toFixed(1)}</TableCell>
            <TableCell className="max-w-48 truncate text-muted-foreground" title={b.linked_rolls.join(", ")}>
              {b.linked_rolls.join(", ") || "—"}
            </TableCell>
          </TableRow>
        ))}
        {rows.length === 0 && (
          <TableRow>
            <TableCell colSpan={6} className="text-muted-foreground">No boxes pushed for this day.</TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  );
}

/** Day picker defaults to the newest pushed day. */
export function ProductionDetail({ days }: { days: Record<string, DayReport> }) {
  const keys = Object.keys(days).sort();
  const latest = keys[keys.length - 1] ?? "";
  const [selected, setSelected] = useState(latest);

  useEffect(() => {
    setSelected(latest);
  }, [latest]);

  const day = days[selected] ?? days[latest];
  if (!day) return null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <label htmlFor="production-day" className="text-sm font-medium">Production day</label>
        <select
          id="production-day"
          className="flex h-9 min-w-48 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
        >
          {keys.map((k) => (
            <option key={k} value={k}>{k}</option>
          ))}
        </select>
        <Badge variant="outline">{keys.length} days available</Badge>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <HugeiconsIcon icon={BoxesIcon} size={18} /> {day.day} — totals
          </CardTitle>
        </CardHeader>
        <CardContent>
          <TotalsStrip totals={day.totals} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Reels · {day.reel_rows.length}</CardTitle>
        </CardHeader>
        <CardContent>
          <ReelRowsTable rows={day.reel_rows} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Boxes · {day.box_rows.length}</CardTitle>
        </CardHeader>
        <CardContent>
          <BoxRowsTable rows={day.box_rows} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">By job</CardTitle>
        </CardHeader>
        <CardContent>
          <ByJobTable day={day} />
        </CardContent>
      </Card>
    </div>
  );
}

// ---------- Orders ----------

export function OrdersTable({ orders }: { orders: OrderProgress[] }) {
  return (
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
        {orders.map((o) => {
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
        {orders.length === 0 && (
          <TableRow>
            <TableCell colSpan={5} className="text-muted-foreground">No orders pushed yet.</TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  );
}

// ---------- Stock ----------

export function StockCards({ stock }: { stock: StockCounts }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <Stat icon={PackageIcon} label="Raw rolls" value={String(stock.raw_rolls)} sub="unpacked stock" />
      <Stat icon={BoxesIcon} label="Packed cartons" value={String(stock.packed_cartons)} sub="ready to dispatch" />
      <Stat icon={DeliveryTruck01Icon} label="Dispatched cartons" value={String(stock.dispatched_cartons)} sub="already shipped" />
    </div>
  );
}

// ---------- Licence (read-only) ----------

export function LicenceTable({ rows }: { rows: LicenceLogRow[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Serial</TableHead>
          <TableHead>Edition</TableHead>
          <TableHead>Issued</TableHead>
          <TableHead>Expires</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((l) => (
          <TableRow key={l.serial + l.issued}>
            <TableCell className="font-medium">{l.serial}</TableCell>
            <TableCell><Badge variant="secondary">{l.edition}</Badge></TableCell>
            <TableCell>{l.issued}</TableCell>
            <TableCell>{l.expires}</TableCell>
          </TableRow>
        ))}
        {rows.length === 0 && (
          <TableRow>
            <TableCell colSpan={4} className="text-muted-foreground">No licence on file.</TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  );
}

// ---------- Downloads ----------

export function ReleaseList({
  releases,
  latest,
}: {
  releases: ReleaseManifest[];
  latest: ReleaseManifest | null;
}) {
  return (
    <>
      {releases.map((r) => (
        <div key={r.file} className="flex flex-col gap-1 rounded-lg border p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="flex items-center gap-2 font-semibold">
              <HugeiconsIcon icon={File01Icon} size={18} className="text-muted-foreground" />
              v{r.version}
              {latest?.file === r.file && <Badge variant="default">latest</Badge>}
            </span>
            <a href={`/api/releases/file/${encodeURIComponent(r.file)}`} download={r.file}>
              <Button size="sm">
                <HugeiconsIcon icon={Download01Icon} size={16} /> {r.file}
              </Button>
            </a>
          </div>
          {r.notes && <p className="text-sm">{r.notes}</p>}
          <p className="break-all text-xs text-muted-foreground">sha256: {r.sha256}</p>
        </div>
      ))}
    </>
  );
}
