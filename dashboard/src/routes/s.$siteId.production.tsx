import { createFileRoute } from "@tanstack/react-router";
import { Skeleton as BoneSkeleton } from "boneyard-js/react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  EmptyState,
  ProductionDetail,
  SiteGate,
  SiteHeading,
} from "@/components/site-portal";
import { boneStyle, mockSnapshot, useSiteSnapshot } from "@/lib/site-data";

export const Route = createFileRoute("/s/$siteId/production")({
  component: SiteProductionRoute,
});

function SiteProductionRoute() {
  const { siteId } = Route.useParams();
  return <SiteProduction siteId={siteId} />;
}

export function SiteProduction({ siteId }: { siteId: string }) {
  const state = useSiteSnapshot(siteId);
  if (state.status === "error" || state.status === "unauthenticated" || state.status === "forbidden")
    return <SiteGate state={state} siteId={siteId} title="Cannot open viewer" />;
  if (state.status === "empty")
    return <EmptyState title="No data yet" description={state.message} />;

  const snap = state.status === "ready" ? state.data : null;
  if (snap && Object.keys(snap.days).length === 0)
    return (
      <EmptyState
        title="No production data yet"
        description="Per-day reel and box figures will appear here after the first sync from the factory."
      />
    );

  return (
    <div className="flex flex-col gap-6">
      <SiteHeading
        siteId={siteId}
        title="Production"
        pushedAt={snap?.pushed_at}
        mock={state.status === "ready" && state.mock}
      />
      <BoneSkeleton
        name="site-production"
        loading={!snap}
        {...boneStyle}
        fixture={<ProductionDetail days={mockSnapshot(siteId).days} />}
        fallback={
          <div className="flex flex-col gap-4">
            <Skeleton className="h-9 w-72" />
            <Skeleton className="h-32" />
            <Skeleton className="h-64" />
            <Skeleton className="h-64" />
          </div>
        }
      >
        {snap ? <ProductionDetail days={snap.days} /> : null}
      </BoneSkeleton>
    </div>
  );
}
