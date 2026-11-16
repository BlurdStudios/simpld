import { createFileRoute } from "@tanstack/react-router";
import { Skeleton as BoneSkeleton } from "boneyard-js/react";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState, SiteHeading, StockCards } from "@/components/site-portal";
import { boneStyle, mockSnapshot, useSiteSnapshot } from "@/lib/site-data";

export const Route = createFileRoute("/s/$siteId/stock")({
  component: SiteStockRoute,
});

function SiteStockRoute() {
  const { siteId } = Route.useParams();
  return <SiteStock siteId={siteId} />;
}

export function SiteStock({ siteId }: { siteId: string }) {
  const state = useSiteSnapshot(siteId);
  if (state.status === "error")
    return <ErrorState title="Cannot open viewer" description={state.message} />;
  if (state.status === "empty")
    return <EmptyState title="No data yet" description={state.message} />;

  const snap = state.status === "ready" ? state.data : null;
  if (snap && !snap.stock_counts)
    return (
      <EmptyState
        title="No stock counts yet"
        description="Raw rolls, packed cartons and dispatches will appear here after the first sync."
      />
    );

  return (
    <div className="flex flex-col gap-6">
      <SiteHeading
        siteId={siteId}
        title="Stock"
        pushedAt={snap?.pushed_at}
        mock={state.status === "ready" && state.mock}
      />
      <BoneSkeleton
        name="site-stock"
        loading={!snap}
        {...boneStyle}
        fixture={<StockCards stock={mockSnapshot(siteId).stock_counts!} />}
        fallback={
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-28" />
            ))}
          </div>
        }
      >
        {snap?.stock_counts ? <StockCards stock={snap.stock_counts} /> : null}
      </BoneSkeleton>
    </div>
  );
}
