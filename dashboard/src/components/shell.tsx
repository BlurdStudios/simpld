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
  PackageIcon,
  BoxesIcon,
  DeliveryTruck01Icon,
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
  /** Only highlight on an exact path match (e.g. the site's Overview). */
  exact?: boolean;
}

// ONE layout, TWO content sets. The route decides which content fills the
// shell: vendor portal pages get the vendor nav, /s/:siteId pages get the
// customer nav (never vendor links). The shell itself only renders what it
// is given — see shellContent() below.
interface ShellContent {
  brand: { title: string; subtitle: string };
  items: NavItem[];
}

function vendorContent(me: { role: string } | null | undefined): ShellContent {
  return {
    brand: { title: "Simpl'd Vendor", subtitle: "factory portal" },
    items: [
      { to: "/", label: "Overview", icon: DashboardSquare01Icon },
      ...(me ? [{ to: "/licence", label: "Licence", icon: Key01Icon }] : []),
      ...(me ? [{ to: "/download", label: "Downloads", icon: Download01Icon }] : []),
      ...(me?.role === "vendor_admin"
        ? [{ to: "/admin", label: "Admin", icon: Shield01Icon }]
        : []),
    ],
  };
}

function customerContent(siteId: string, onLogin: boolean, me: { role: string } | null | undefined): ShellContent {
  const base = `/s/${siteId}`;
  return {
    brand: { title: siteId, subtitle: "customer portal" },
    // The login page shows branding only — nav appears once inside the site.
    items: onLogin
      ? []
      : [
          { to: base, label: "Overview", icon: DashboardSquare01Icon, exact: true },
          { to: `${base}/production`, label: "Production", icon: BoxesIcon },
          { to: `${base}/orders`, label: "Orders", icon: DeliveryTruck01Icon },
          { to: `${base}/stock`, label: "Stock", icon: PackageIcon },
          { to: `${base}/licence`, label: "Licence", icon: Key01Icon },
          { to: `${base}/downloads`, label: "Downloads", icon: Download01Icon },
          // A vendor admin previewing a customer's site gets a way back.
          ...(me?.role === "vendor_admin"
            ? [{ to: "/", label: "Vendor portal", icon: ArrowLeft01Icon, exact: true }]
            : []),
        ],
  };
}

function shellContent(pathname: string, me: { role: string } | null | undefined): ShellContent {
  // Any /s/:siteId/... path is a customer page; the login subpath shows
  // branding only (no nav until the customer is inside the site).
  const siteMatch = /^\/s\/([^/]+)(\/.*)?$/.exec(pathname);
  if (siteMatch) {
    const siteId = decodeURIComponent(siteMatch[1]);
    return customerContent(siteId, /^\/login\/?$/.test(siteMatch[2] ?? ""), me);
  }
  return vendorContent(me);
}

export function DashboardShell({ children }: { children: React.ReactNode }) {
  const { me, logout } = useAuth();
  const location = useLocation();
  const [open, setOpen] = useState(false);

  const content = shellContent(location.pathname, me);

  const sidebar = (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-center gap-2 px-2 py-1">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-lg font-bold text-primary-foreground">
          S
        </div>
        <div className="leading-tight">
          <p className="truncate text-sm font-semibold">{content.brand.title}</p>
          <p className="text-xs text-muted-foreground">{content.brand.subtitle}</p>
        </div>
      </div>
      <Separator />
      <nav className="flex flex-col gap-1">
        {content.items.map((n) => {
          // Exact-only for Overview (and the vendor root) so a subpage never
          // lights up its parent. Other items keep the prefix behaviour.
          const active = n.to === "/" || n.exact
            ? location.pathname === n.to
            : location.pathname.startsWith(n.to);
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
        <p className="truncate text-sm font-semibold">{content.brand.title}</p>
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
