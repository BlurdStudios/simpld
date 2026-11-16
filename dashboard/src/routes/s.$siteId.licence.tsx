import { createFileRoute } from "@tanstack/react-router";
import { Skeleton as BoneSkeleton } from "boneyard-js/react";
import { HugeiconsIcon } from "@hugeicons/react";
import { Key01Icon } from "@hugeicons/core-free-icons";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState, LicenceTable, SiteHeading } from "@/components/site-portal";
import { boneStyle, mockLicences, useSiteLicences } from "@/lib/site-data";

export const Route = createFileRoute("/s/$siteId/licence")({
  component: SiteLicenceRoute,
});

function SiteLicenceRoute() {
  const { siteId } = Route.useParams();
  return <SiteLicence siteId={siteId} />;
}

export function SiteLicence({ siteId }: { siteId: string }) {
  const state = useSiteLicences(siteId);
  if (state.status === "error")
    return <ErrorState title="Cannot load licences" description={state.message} />;
  if (state.status === "empty")
    return <EmptyState title="No licence on file" description={state.message} />;

  const rows = state.status === "ready" ? state.data : null;
  if (rows && rows.length === 0)
    return (
      <EmptyState
        title="No licence on file"
        description="Your vendor admin will issue a licence and it will be delivered to the factory automatically."
      />
    );

  return (
    <div className="flex flex-col gap-6">
      <SiteHeading
        siteId={siteId}
        title="Licence"
        description="Licences issued for this site. Read-only — contact your vendor admin to change one."
      />
      <BoneSkeleton
        name="site-licence"
        loading={!rows}
        {...boneStyle}
        fixture={
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <HugeiconsIcon icon={Key01Icon} size={18} /> Issued licences
              </CardTitle>
            </CardHeader>
            <CardContent>
              <LicenceTable rows={mockLicences(siteId)} />
            </CardContent>
          </Card>
        }
        fallback={
          <div className="flex flex-col gap-3">
            <Skeleton className="h-12" />
            {[0, 1].map((i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        }
      >
        {rows ? (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <HugeiconsIcon icon={Key01Icon} size={18} /> Issued licences
              </CardTitle>
            </CardHeader>
            <CardContent>
              <LicenceTable rows={rows} />
            </CardContent>
          </Card>
        ) : null}
      </BoneSkeleton>
    </div>
  );
}
