import { createFileRoute } from "@tanstack/react-router";
import { Skeleton as BoneSkeleton } from "boneyard-js/react";
import { HugeiconsIcon } from "@hugeicons/react";
import { DeliveryTruck01Icon } from "@hugeicons/core-free-icons";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, OrdersTable, SiteGate, SiteHeading } from "@/components/site-portal";
import { boneStyle, mockSnapshot, useSiteSnapshot } from "@/lib/site-data";

export const Route = createFileRoute("/s/$siteId/orders")({
  component: SiteOrdersRoute,
});

function SiteOrdersRoute() {
  const { siteId } = Route.useParams();
  return <SiteOrders siteId={siteId} />;
}

export function SiteOrders({ siteId }: { siteId: string }) {
  const state = useSiteSnapshot(siteId);
  if (state.status === "error" || state.status === "unauthenticated" || state.status === "forbidden")
    return <SiteGate state={state} siteId={siteId} title="Cannot open viewer" />;
  if (state.status === "empty")
    return <EmptyState title="No data yet" description={state.message} />;

  const snap = state.status === "ready" ? state.data : null;
  if (snap && snap.orders.length === 0)
    return (
      <EmptyState
        title="No orders yet"
        description="Orders and their loaded/target progress will appear here after the first sync."
      />
    );

  return (
    <div className="flex flex-col gap-6">
      <SiteHeading
        siteId={siteId}
        title="Orders"
        pushedAt={snap?.pushed_at}
        mock={state.status === "ready" && state.mock}
      />
      <BoneSkeleton
        name="site-orders"
        loading={!snap}
        {...boneStyle}
        fixture={
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <HugeiconsIcon icon={DeliveryTruck01Icon} size={18} /> Orders — loaded / target
              </CardTitle>
            </CardHeader>
            <CardContent>
              <OrdersTable orders={mockSnapshot(siteId).orders} />
            </CardContent>
          </Card>
        }
        fallback={
          <div className="flex flex-col gap-3">
            <Skeleton className="h-12" />
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        }
      >
        {snap ? (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <HugeiconsIcon icon={DeliveryTruck01Icon} size={18} /> Orders — loaded / target
              </CardTitle>
            </CardHeader>
            <CardContent>
              <OrdersTable orders={snap.orders} />
            </CardContent>
          </Card>
        ) : null}
      </BoneSkeleton>
    </div>
  );
}
