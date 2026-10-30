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

const STATION_SUMMARY: Record<string, string> = {
  simple: "1 station · no LAN",
  pro: "3 stations · LAN",
  premium: "5 stations · LAN + API",
  trial: "1 station · demo",
};

const VALIDITY_PRESETS = [
  { label: "6 months", days: 182 },
  { label: "1 year", days: 365 },
  { label: "2 years", days: 730 },
  { label: "3 years", days: 1095 },
];

function expiryPreview(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export function Licence() {
  const { me } = useAuth();
  const [siteId, setSiteId] = useState("");
  const [customer, setCustomer] = useState("");
  const [packagerFp, setPackagerFp] = useState("");
  const [edition, setEdition] = useState("pro");
  const [trialDays, setTrialDays] = useState("14");
  const [validityDays, setValidityDays] = useState(365);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [licence, setLicence] = useState<string | null>(null);

  const lockedSite = me?.role === "client" ? (me.site_id ?? "") : null;
  const effectiveSite = lockedSite || siteId.trim();
  const fp = packagerFp.trim();
  const fpOk = /^[0-9a-fA-F]{32}$/.test(fp);
  const effTrialDays = Math.min(365, Math.max(1, Number(trialDays) || 14));
  const effDays = edition === "trial" ? effTrialDays : validityDays;

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
          packager_fp: fp,
          edition,
          ...(edition === "trial" ? { trial_days: effTrialDays } : { validity_days: effDays }),
        }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.error ?? `issuance failed (${res.status})`);
      const text = JSON.stringify(data.licence);
      setLicence(text);
      // Auto-download the .lic file — no extra click.
      const blob = new Blob([text], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "license.lic";
      a.click();
      URL.revokeObjectURL(a.href);
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
            No internet needed on the factory PC — the key verifies offline. The file downloads automatically.
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
                onChange={(e) => setPackagerFp(e.target.value.trim())}
              />
              {fp.length > 0 && !fpOk && (
                <p className="text-xs font-medium text-destructive">
                  Machine ID must be exactly 32 hex characters — copy it whole from the About screen.
                </p>
              )}
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
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
              </div>
              {edition === "trial" ? (
                <div className="grid gap-2">
                  <Label htmlFor="lic-trialdays">Trial length (days, 1–365)</Label>
                  <div className="flex flex-wrap gap-2">
                    {[4, 7, 14, 30].map((n) => (
                      <Button
                        key={n}
                        type="button"
                        variant={effTrialDays === n ? "default" : "outline"}
                        onClick={() => setTrialDays(String(n))}
                      >
                        {n === 7 ? "1 week" : `${n} days`}
                      </Button>
                    ))}
                  </div>
                  <Input
                    id="lic-trialdays"
                    inputMode="numeric"
                    value={trialDays}
                    onChange={(e) => setTrialDays(e.target.value.replace(/[^0-9]/g, ""))}
                  />
                </div>
              ) : (
                <div className="grid gap-2">
                  <Label>Valid for</Label>
                  <select
                    className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
                    value={validityDays}
                    onChange={(e) => setValidityDays(Number(e.target.value))}
                  >
                    {VALIDITY_PRESETS.map((p) => (
                      <option key={p.days} value={p.days}>{p.label}</option>
                    ))}
                  </select>
                </div>
              )}
            </div>
            <p className="text-sm text-muted-foreground">
              {edition} · {STATION_SUMMARY[edition]} · starts today, {effDays} days → expires {expiryPreview(effDays)} (auto).
              {edition === "trial" && " App shows a countdown."}
            </p>
            {error && <p className="text-sm font-medium text-destructive">{error}</p>}
            <div>
              <Button disabled={busy || !effectiveSite || !fpOk || !customer.trim()}>
                Issue {edition === "trial" ? `trial (${effTrialDays}d)` : `licence (${effDays}d)`}
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
