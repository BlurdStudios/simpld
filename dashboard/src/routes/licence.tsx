import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { HugeiconsIcon } from "@hugeicons/react";
import { Key01Icon, Copy01Icon, Download01Icon } from "@hugeicons/core-free-icons";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RequireAuth, useAuth } from "@/lib/session";

export const Route = createFileRoute("/licence")({
  component: () => (
    <RequireAuth>
      <Licence />
    </RequireAuth>
  ),
});

export function Licence() {
  const { me } = useAuth();
  const [siteId, setSiteId] = useState("");
  const [customer, setCustomer] = useState("");
  const [packagerFp, setPackagerFp] = useState("");
  const [edition, setEdition] = useState("pro");
  const [trialDays, setTrialDays] = useState("14");
  const [maxStations, setMaxStations] = useState("2");
  const [expires, setExpires] = useState("");
  const [maxVersion, setMaxVersion] = useState("2.x");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [licence, setLicence] = useState<string | null>(null);

  const lockedSite = me?.role === "client" ? (me.site_id ?? "") : null;
  const effectiveSite = lockedSite || siteId.trim();

  async function issue(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setLicence(null);
    try {
      const res = await fetch("/api/licenses/issue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          site_id: effectiveSite,
          customer,
          packager_fp: packagerFp.trim(),
          edition,
          max_stations: Number(maxStations),
          expires,
          max_version: maxVersion,
          ...(edition === "trial" ? { trial_days: Number(trialDays) || 14 } : {}),
        }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.error ?? `issuance failed (${res.status})`);
      setLicence(JSON.stringify(data.licence));
    } catch (err) {
      setError(err instanceof Error ? err.message : "issuance failed");
    } finally {
      setBusy(false);
    }
  }

  function download() {
    if (!licence) return;
    const blob = new Blob([licence], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "license.lic";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Issue licence</h1>
        <p className="text-sm text-muted-foreground">
          Signed by the vendor key for one Machine ID (packager_fp). Logged as {me?.email}.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">How licensing works (3 steps)</CardTitle>
          <CardDescription>
            1. Copy the Machine ID from the packager About screen. 2. Issue a licence here for that ID.
            3. On the packager, open Activate (or About → Import licence) and paste the key or import the file.
            No internet needed on the factory PC — the key verifies offline. Trial = N-day demo key
            (single station, app shows a countdown); simple/pro/premium = 1-year keys from the tier rules.
          </CardDescription>
        </CardHeader>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <HugeiconsIcon icon={Key01Icon} size={18} /> New licence
          </CardTitle>
          <CardDescription>
            {lockedSite ? `Locked to your site ${lockedSite}.` : "Any site (vendor admin)."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={issue} className="grid gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="lic-site">Site ID</Label>
                <Input
                  id="lic-site"
                  placeholder="e.g. vasavi-f1"
                  value={lockedSite || siteId}
                  disabled={lockedSite !== null}
                  onChange={(e) => setSiteId(e.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="lic-customer">Customer name</Label>
                <Input
                  id="lic-customer"
                  placeholder="e.g. Vasavi Foods"
                  value={customer}
                  onChange={(e) => setCustomer(e.target.value)}
                />
              </div>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="lic-fp">Machine ID (packager_fp) — shown on the packager About screen</Label>
              <Input
                id="lic-fp"
                placeholder="32-char board fingerprint"
                value={packagerFp}
                onChange={(e) => setPackagerFp(e.target.value)}
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <div className="grid gap-2">
                <Label>Edition</Label>
                <select
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
                  value={edition}
                  onChange={(e) => setEdition(e.target.value)}
                >
                  <option value="simple">simple — 1 station, no LAN</option>
                  <option value="pro">pro — 3 stations, LAN</option>
                  <option value="premium">premium — 5 stations, LAN+API</option>
                  <option value="trial">trial — 1 station, N-day demo</option>
                </select>
                <p className="text-xs text-muted-foreground">
                  Stations/features/expiry come from the tier — you only pick site, customer, Machine ID and edition.
                </p>
              </div>
              {edition === "trial" ? (
                <div className="grid gap-2">
                  <Label htmlFor="lic-trialdays">Trial days (1–365)</Label>
                  <Input
                    id="lic-trialdays"
                    inputMode="numeric"
                    value={trialDays}
                    onChange={(e) => setTrialDays(e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">App shows a countdown for these many days.</p>
                </div>
              ) : (
                <div className="grid gap-2">
                  <Label htmlFor="lic-stations">Max stations</Label>
                  <Input
                    id="lic-stations"
                    inputMode="numeric"
                    value={maxStations}
                    onChange={(e) => setMaxStations(e.target.value)}
                  />
                </div>
              )}
              <div className="grid gap-2">
                <Label htmlFor="lic-expires">Expires (YYYY-MM-DD)</Label>
                <Input
                  id="lic-expires"
                  placeholder="2027-10-26"
                  value={expires}
                  onChange={(e) => setExpires(e.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="lic-maxver">Max version</Label>
                <Input
                  id="lic-maxver"
                  value={maxVersion}
                  onChange={(e) => setMaxVersion(e.target.value)}
                />
              </div>
            </div>
            {error && <p className="text-sm font-medium text-destructive">{error}</p>}
            <div>
              <Button disabled={busy || !effectiveSite || !packagerFp.trim() || (edition !== "trial" && !expires) || !customer.trim()}>
                Issue {edition === "trial" ? `trial (${trialDays || 14}d)` : "licence"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
      {licence && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Licence issued — activate the site</CardTitle>
            <CardDescription>
              Paste the key or import the file on the packager Activate screen — same bytes either way.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="flex flex-wrap gap-2">
              <Button onClick={download}>
                <HugeiconsIcon icon={Download01Icon} size={16} /> Download license.lic
              </Button>
              <Button
                variant="outline"
                onClick={() => navigator.clipboard?.writeText(licence).catch(() => {})}
              >
                <HugeiconsIcon icon={Copy01Icon} size={16} /> Copy key text
              </Button>
            </div>
            <pre className="overflow-auto rounded-md border bg-muted p-4 text-xs whitespace-pre-wrap break-all">
              {licence}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
