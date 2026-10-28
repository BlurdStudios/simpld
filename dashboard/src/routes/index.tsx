import { createFileRoute, Link } from "@tanstack/react-router";
import { HugeiconsIcon } from "@hugeicons/react";
import {
  ViewIcon,
  Key01Icon,
  Download01Icon,
  Shield01Icon,
} from "@hugeicons/core-free-icons";
import { Button } from "@/components/ui/button";
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
            <Link to="/s/$siteId" params={{ siteId: clientSite }} search={{ key: "" }}>
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

  // Logged out: product hero + login. No public key form — every client gets
  // a provisioned account; shared ?key= viewer links still work directly.
  return (
    <div className="mx-auto flex max-w-lg flex-col justify-center gap-6 py-8">
      <div className="text-center">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-primary text-xl font-bold text-primary-foreground">
          S
        </div>
        <h1 className="text-2xl font-bold tracking-tight">Simpl'd factory portal</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Vendor-hosted viewer + self-serve station. Cached pushes only — no live
          factory queries. Client logins are provisioned by your vendor admin.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Log in to continue</CardTitle>
          <CardDescription>Licence issuance, downloads and site viewing need a login.</CardDescription>
        </CardHeader>
        <CardContent>
          <Link to="/login" search={{ redirect: "/" }}>
            <Button className="w-full">Log in</Button>
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
