import { useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { HugeiconsIcon } from "@hugeicons/react";
import {
  ViewIcon,
  Key01Icon,
  Download01Icon,
  Shield01Icon,
} from "@hugeicons/core-free-icons";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/session";

export const Route = createFileRoute("/")({
  component: LandingRoute,
});

function LandingRoute() {
  return <Landing />;
}

export function Landing() {
  const { me } = useAuth();
  const navigate = useNavigate();
  const [siteId, setSiteId] = useState("");

  if (me === undefined) return null;

  // Logged in: role-aware overview.
  if (me) {
    const clientSite = me.role === "client" ? me.site_id : null;
    return (
      <div className="flex flex-col gap-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Overview</h1>
          <p className="text-sm text-muted-foreground">
            Signed in as {me.email}
            {clientSite ? ` · site ${clientSite}` : ""}
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {clientSite && (
            <Link to="/s/$siteId" params={{ siteId: clientSite }}>
              <Card className="transition-colors hover:border-primary">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <HugeiconsIcon icon={ViewIcon} size={18} /> My site
                  </CardTitle>
                  <CardDescription>Yesterday + today production, orders, stock.</CardDescription>
                </CardHeader>
              </Card>
            </Link>
          )}
          <Link to="/licence">
            <Card className="transition-colors hover:border-primary">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <HugeiconsIcon icon={Key01Icon} size={18} /> Licence
                </CardTitle>
                <CardDescription>Issue a signed licence for a Machine ID.</CardDescription>
              </CardHeader>
            </Card>
          </Link>
          <Link to="/download">
            <Card className="transition-colors hover:border-primary">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <HugeiconsIcon icon={Download01Icon} size={18} /> Downloads
                </CardTitle>
                <CardDescription>Latest Simpl'd installer, SHA-256 verified.</CardDescription>
              </CardHeader>
            </Card>
          </Link>
          {me.role === "vendor_admin" && (
            <Link to="/admin">
              <Card className="transition-colors hover:border-primary">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <HugeiconsIcon icon={Shield01Icon} size={18} /> Admin
                  </CardTitle>
                  <CardDescription>Users, keys, sites, licences, releases.</CardDescription>
                </CardHeader>
              </Card>
            </Link>
          )}
        </div>
      </div>
    );
  }

  // Logged out: product hero + the SITE-ID login form. The vendor login is
  // never offered here (or anywhere public) — vendor staff use the unlinked
  // /admin/login directly. No self-registration; every client gets a
  // provisioned account.
  return (
    <div className="mx-auto flex max-w-lg flex-col justify-center gap-6 py-8">
      <div className="text-center">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-primary text-xl font-bold text-primary-foreground">
          S
        </div>
        <h1 className="text-2xl font-bold tracking-tight">Simpl'd factory portal</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Cached pushes only — no live factory queries. Logins are provisioned
          by your vendor.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Log in to your site</CardTitle>
          <CardDescription>Enter your site ID to continue.</CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              const id = siteId.trim();
              if (id) navigate({ to: "/s/$siteId/login", params: { siteId: id } });
            }}
          >
            <Input
              value={siteId}
              onChange={(e) => setSiteId(e.target.value)}
              placeholder="Site ID (e.g. VASAVI)"
            />
            <Button type="submit" className="w-full" disabled={!siteId.trim()}>
              Continue
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
