import { createFileRoute } from "@tanstack/react-router";
import { Skeleton as BoneSkeleton } from "boneyard-js/react";
import { HugeiconsIcon } from "@hugeicons/react";
import { Download01Icon } from "@hugeicons/core-free-icons";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ReleaseList, SiteGate, SiteHeading } from "@/components/site-portal";
import { boneStyle, MOCK_RELEASES, useSiteReleases } from "@/lib/site-data";

export const Route = createFileRoute("/s/$siteId/downloads")({
  component: SiteDownloadsRoute,
});

function SiteDownloadsRoute() {
  const { siteId } = Route.useParams();
  return <SiteDownloads siteId={siteId} />;
}

export function SiteDownloads({ siteId }: { siteId: string }) {
  const state = useSiteReleases();
  if (state.status === "error" || state.status === "unauthenticated" || state.status === "forbidden")
    return <SiteGate state={state} siteId={siteId} title="Cannot open downloads" />;
  if (state.status === "empty")
    return <EmptyState title="No downloads yet" description={state.message} />;

  const data = state.status === "ready" ? state.data : null;
  if (data && data.releases.length === 0)
    return (
      <EmptyState
        title="No downloads yet"
        description="Installers published by your vendor admin will appear here, SHA-256 verified by the updater."
      />
    );

  return (
    <div className="flex flex-col gap-6">
      <SiteHeading
        siteId={siteId}
        title="Downloads"
        description="Installers for this site. The updater verifies SHA-256 before installing."
      />
      <BoneSkeleton
        name="site-downloads"
        loading={!data}
        {...boneStyle}
        fixture={
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <HugeiconsIcon icon={Download01Icon} size={18} /> Releases
              </CardTitle>
              <CardDescription>Compare the hash below if in doubt.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3">
              <ReleaseList releases={MOCK_RELEASES} latest={MOCK_RELEASES[0]} />
            </CardContent>
          </Card>
        }
        fallback={
          <div className="flex flex-col gap-3">
            <Skeleton className="h-20" />
            <Skeleton className="h-20" />
          </div>
        }
      >
        {data ? (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <HugeiconsIcon icon={Download01Icon} size={18} /> Releases
              </CardTitle>
              <CardDescription>Compare the hash below if in doubt.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3">
              <ReleaseList releases={data.releases} latest={data.latest} />
            </CardContent>
          </Card>
        ) : null}
      </BoneSkeleton>
    </div>
  );
}
