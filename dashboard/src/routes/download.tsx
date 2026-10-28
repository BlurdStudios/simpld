import { useEffect, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { HugeiconsIcon } from "@hugeicons/react";
import { Download01Icon, File01Icon } from "@hugeicons/core-free-icons";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { ReleaseManifest } from "@/lib/contract";
import { RequireAuth, useAuth } from "@/lib/session";

export const Route = createFileRoute("/download")({
  component: () => (
    <RequireAuth>
      <Downloads />
    </RequireAuth>
  ),
});

export function Downloads() {
  const { me } = useAuth();
  const [latest, setLatest] = useState<ReleaseManifest | null>(null);
  const [releases, setReleases] = useState<ReleaseManifest[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const res = await fetch("/api/releases");
        const data = await res.json().catch(() => null);
        if (!res.ok) throw new Error(data?.error ?? `releases failed (${res.status})`);
        if (live) {
          setLatest(data.latest ?? null);
          setReleases(data.releases ?? []);
          setError(null);
        }
      } catch (err) {
        if (live) setError(err instanceof Error ? err.message : "releases failed");
      } finally {
        if (live) setLoading(false);
      }
    })();
    return () => {
      live = false;
    };
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Downloads</h1>
        <p className="text-sm text-muted-foreground">
          Installers for {me?.email}. SHA-256-verified by the updater before install.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <HugeiconsIcon icon={Download01Icon} size={18} /> Releases
          </CardTitle>
          <CardDescription>Compare the hash below if in doubt (see UPDATES.md).</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          {loading && (
            <>
              <Skeleton className="h-20" />
              <Skeleton className="h-20" />
            </>
          )}
          {error && <p className="text-sm font-medium text-destructive">{error}</p>}
          {!loading && !error && releases.length === 0 && (
            <p className="text-sm text-muted-foreground">No releases published yet.</p>
          )}
          {releases.map((r) => (
            <div key={r.file} className="flex flex-col gap-1 rounded-lg border p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="flex items-center gap-2 font-semibold">
                  <HugeiconsIcon icon={File01Icon} size={18} className="text-muted-foreground" />
                  v{r.version}
                  {latest?.file === r.file && (
                    <span className="rounded-md bg-primary px-2 py-0.5 text-xs font-semibold text-primary-foreground">
                      latest
                    </span>
                  )}
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
        </CardContent>
      </Card>
    </div>
  );
}
