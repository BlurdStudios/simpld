import { createFileRoute } from "@tanstack/react-router";
import { Skeleton as BoneSkeleton } from "boneyard-js/react";
import {
  PackageIcon,
  BoxesIcon,
  Chart01Icon,
  Clock01Icon,
} from "@hugeicons/core-free-icons";
import { Skeleton } from "@/components/ui/skeleton";
import {
  EmptyState,
  ErrorState,
  LatestDaySummary,
  SiteHeading,
  Stat,
} from "@/components/site-portal";
import { boneStyle, mockSnapshot, useSiteSnapshot } from "@/lib/site-data";
import type { SiteSnapshot } from "@/lib/contract";

export const Route = createFileRoute("/s/$siteId/")({
  component: SiteOverviewRoute,
});

function SiteOverviewRoute() {
  const { siteId } = Route.useParams();
  return <SiteOverview siteId={siteId} />;
}

/** Overview: headline stats + compact latest-day summary (no big tables). */
function OverviewContent({ snap, mock }: { snap: SiteSnapshot; mock: boolean }) {
  const dayKeys = Object.keys(snap.days).sort();
  const latest = dayKeys.length ? snap.days[dayKeys[dayKeys.length - 1]] : null;
  const inProgress = snap.orders.filter((o) => o.status !== "complete").length;

  return (
    <div className="flex flex-col gap-6">
      <SiteHeading siteId={snap.site_id} pushedAt={snap.pushed_at} mock={mock} />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
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
          value={String(inProgress)}
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
        <Stat
          icon={Clock01Icon}
          label="Days tracked"
          value={String(dayKeys.length)}
          sub={dayKeys.length ? `latest ${dayKeys[dayKeys.length - 1]}` : undefined}
        />
      </div>
      {latest && <LatestDaySummary day={latest} />}
    </div>
  );
}

export function SiteOverview({ siteId }: { siteId: string }) {
  const state = useSiteSnapshot(siteId);
  if (state.status === "error")
    return <ErrorState title="Cannot open viewer" description={state.message} />;
  if (state.status === "empty")
    return <EmptyState title="No data yet" description={state.message} />;

  const snap = state.status === "ready" ? state.data : null;
  return (
    <BoneSkeleton
      name="site-overview"
      loading={!snap}
      {...boneStyle}
      fixture={<OverviewContent snap={mockSnapshot(siteId)} mock />}
      fallback={
        <div className="flex flex-col gap-6">
          <Skeleton className="h-16 w-72" />
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-28" />
            ))}
          </div>
          <Skeleton className="h-56" />
        </div>
      }
    >
      {snap ? <OverviewContent snap={snap} mock={state.status === "ready" && state.mock} /> : null}
    </BoneSkeleton>
  );
}
