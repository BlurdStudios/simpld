import { useState } from "react";
import { Link, useLocation } from "@tanstack/react-router";
import { HugeiconsIcon } from "@hugeicons/react";
import {
  DashboardSquare01Icon,
  Key01Icon,
  Download01Icon,
  Shield01Icon,
  Logout01Icon,
  Menu01Icon,
  Cancel01Icon,
  ArrowLeft01Icon,
} from "@hugeicons/core-free-icons";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { useAuth } from "@/lib/session";

interface NavItem {
  to: string;
  label: string;
  icon: typeof DashboardSquare01Icon;
  adminOnly?: boolean;
  authOnly?: boolean;
}

const NAV: NavItem[] = [
  { to: "/", label: "Overview", icon: DashboardSquare01Icon },
  { to: "/licence", label: "Licence", icon: Key01Icon, authOnly: true },
  { to: "/download", label: "Downloads", icon: Download01Icon, authOnly: true },
  { to: "/admin", label: "Admin", icon: Shield01Icon, adminOnly: true },
];

export function DashboardShell({ children }: { children: React.ReactNode }) {
  const { me, logout } = useAuth();
  const location = useLocation();
  const [open, setOpen] = useState(false);

  // Route-aware chrome: /s/:siteId (and /s/:siteId/login) are the CUSTOMER's
  // pages — they get customer branding and a customer nav, never the vendor
  // portal links (Licence/Downloads/Admin). Everything else keeps the vendor
  // sidebar. A vendor_admin previewing a site gets a link back to the portal.
  const siteMatch = /^\/s\/([^/]+)(\/login)?$/.exec(location.pathname);
  const siteId = siteMatch ? decodeURIComponent(siteMatch[1]) : null;
  const onSiteLogin = !!siteId && /\/login$/.test(location.pathname);

  const items: NavItem[] = siteId
    ? onSiteLogin
      ? []
      : [
          { to: `/s/${siteId}`, label: "Overview", icon: DashboardSquare01Icon },
          ...(me?.role === "vendor_admin"
            ? [{ to: "/", label: "Vendor portal", icon: ArrowLeft01Icon }]
            : []),
        ]
    : NAV.filter((n) => {
        if (n.adminOnly) return me?.role === "vendor_admin";
        if (n.authOnly) return !!me;
        return true;
      });

  const sidebar = (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-center gap-2 px-2 py-1">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-lg font-bold text-primary-foreground">
          S
        </div>
        <div className="leading-tight">
          <p className="truncate text-sm font-semibold">{siteId ?? "Simpl'd Vendor"}</p>
          <p className="text-xs text-muted-foreground">
            {siteId ? "customer portal" : "factory portal"}
          </p>
        </div>
      </div>
      <Separator />
      <nav className="flex flex-col gap-1">
        {items.map((n) => {
          const active =
            n.to === "/" ? location.pathname === "/" : location.pathname.startsWith(n.to);
          return (
            <Link
              key={n.to}
              to={n.to}
              onClick={() => setOpen(false)}
              className={cn(
                "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/60 hover:text-accent-foreground"
              )}
            >
              <HugeiconsIcon icon={n.icon} size={18} />
              {n.label}
            </Link>
          );
        })}
      </nav>
      <div className="mt-auto flex flex-col gap-2">
        <Separator />
        {me ? (
          <div className="flex items-center justify-between gap-2 px-1">
            <div className="min-w-0 leading-tight">
              <p className="truncate text-sm font-medium">{me.email}</p>
              <Badge variant={me.role === "vendor_admin" ? "default" : "secondary"}>
                {me.role === "vendor_admin" ? "vendor admin" : `client${me.site_id ? ` · ${me.site_id}` : ""}`}
              </Badge>
            </div>
            <Button variant="ghost" size="icon" title="Log out" onClick={logout}>
              <HugeiconsIcon icon={Logout01Icon} size={18} />
            </Button>
          </div>
        ) : (
          <Link to="/login" search={{ redirect: location.pathname }} onClick={() => setOpen(false)}>
            <Button className="w-full">Log in</Button>
          </Link>
        )}
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-background">
      {/* desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 hidden w-60 border-r bg-card p-4 md:block">
        {sidebar}
      </aside>
      {/* mobile top bar */}
      <header className="sticky top-0 z-10 flex items-center gap-2 border-b bg-card/95 px-4 py-2 backdrop-blur md:hidden">
        <Button variant="ghost" size="icon" onClick={() => setOpen(!open)} aria-label="Menu">
          <HugeiconsIcon icon={open ? Cancel01Icon : Menu01Icon} size={20} />
        </Button>
        <p className="text-sm font-semibold">{siteId ?? "Simpl'd Vendor"}</p>
        <div className="ml-auto">
          {me ? (
            <Badge variant={me.role === "vendor_admin" ? "default" : "secondary"}>
              {me.role === "vendor_admin" ? "admin" : (me.site_id ?? "client")}
            </Badge>
          ) : (
            <Link to="/login" search={{ redirect: "/" }}>
              <Button size="sm">Log in</Button>
            </Link>
          )}
        </div>
      </header>
      {open && (
        <div className="border-b bg-card p-4 md:hidden" onClick={() => setOpen(false)}>
          {sidebar}
        </div>
      )}
      <main className="p-4 md:ml-60 md:p-8">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  );
}
