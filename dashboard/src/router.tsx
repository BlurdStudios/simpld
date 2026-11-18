// Code-based router (works with or without the TanStack codegen plugin).
// File-based routes in src/routes/* remain the canonical layout: when
// @tanstack/router-plugin runs, it regenerates routeTree.gen.ts and main.tsx
// prefers it. This fallback keeps `npm run dev` working offline/from scratch.
// Keep the two route trees in sync when adding a page.
import {
  Link,
  Navigate,
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
  useParams,
} from "@tanstack/react-router";
import { AuthProvider, RequireAdmin, RequireAuth, meSession } from "@/lib/session";
import { DashboardShell } from "@/components/shell";
import { Landing } from "./routes/index";
import { SiteOverview } from "./routes/s.$siteId.index";
import { SiteProduction } from "./routes/s.$siteId.production";
import { SiteOrders } from "./routes/s.$siteId.orders";
import { SiteStock } from "./routes/s.$siteId.stock";
import { SiteLicence } from "./routes/s.$siteId.licence";
import { SiteDownloads } from "./routes/s.$siteId.downloads";
import { SiteLogin } from "./routes/s.$siteId.login";
import { AdminLogin } from "./routes/admin.login";
import { Licence } from "./routes/licence";
import { Downloads } from "./routes/download";
import { Admin } from "./routes/admin.index";

const rootRoute = createRootRoute({
  component: () => (
    <AuthProvider>
      <DashboardShell>
        <Outlet />
      </DashboardShell>
    </AuthProvider>
  ),
});

const landingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: () => {
    return <Landing />;
  },
});

// Customer portal layout — each page below is a real child route.
const siteRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/s/$siteId",
  // Preemptive auth gate (the API 401 handling in site-data stays as the
  // runtime backstop). Site users NEVER see vendor login: an unauthenticated
  // visitor goes straight to THIS site's login, and a client poking another
  // site's URL bounces to their own portal. vendor_admin passes through —
  // the page's 403 card handles what they may not read.
  beforeLoad: async ({ params }) => {
    const me = await meSession();
    const siteId = (params as { siteId: string }).siteId;
    if (!me) {
      throw redirect({ to: "/s/$siteId/login", params: { siteId } });
    }
    if (me.role === "client" && me.site_id && me.site_id !== siteId) {
      throw redirect({ to: "/s/$siteId", params: { siteId: me.site_id } });
    }
  },
  component: () => <Outlet />,
});

const siteIndexRoute = createRoute({
  getParentRoute: () => siteRoute,
  path: "/",
  component: () => {
    const { siteId } = useParams({ from: siteIndexRoute.id });
    return <SiteOverview siteId={siteId} />;
  },
});

const siteProductionRoute = createRoute({
  getParentRoute: () => siteRoute,
  path: "/production",
  component: () => {
    const { siteId } = useParams({ from: siteProductionRoute.id });
    return <SiteProduction siteId={siteId} />;
  },
});

const siteOrdersRoute = createRoute({
  getParentRoute: () => siteRoute,
  path: "/orders",
  component: () => {
    const { siteId } = useParams({ from: siteOrdersRoute.id });
    return <SiteOrders siteId={siteId} />;
  },
});

const siteStockRoute = createRoute({
  getParentRoute: () => siteRoute,
  path: "/stock",
  component: () => {
    const { siteId } = useParams({ from: siteStockRoute.id });
    return <SiteStock siteId={siteId} />;
  },
});

const siteLicenceRoute = createRoute({
  getParentRoute: () => siteRoute,
  path: "/licence",
  component: () => {
    const { siteId } = useParams({ from: siteLicenceRoute.id });
    return <SiteLicence siteId={siteId} />;
  },
});

const siteDownloadsRoute = createRoute({
  getParentRoute: () => siteRoute,
  path: "/downloads",
  component: () => {
    const { siteId } = useParams({ from: siteDownloadsRoute.id });
    return <SiteDownloads siteId={siteId} />;
  },
});

const siteRouteWithChildren = siteRoute.addChildren([
  siteIndexRoute,
  siteProductionRoute,
  siteOrdersRoute,
  siteStockRoute,
  siteLicenceRoute,
  siteDownloadsRoute,
]);

const siteLoginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/s/$siteId/login",
  component: () => {
    const { siteId } = useParams({ from: siteLoginRoute.id });
    return <SiteLogin />;
  },
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  validateSearch: (s: Record<string, unknown>) => ({
    redirect: (s.redirect as string) ?? "/",
  }),
  // Legacy entry point. Never points at the vendor login — site users get
  // the landing page (site-ID login); vendor staff use /admin/login direct.
  component: () => <Navigate to="/" replace />,
});

const licenceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/licence",
  beforeLoad: async () => {
    // Licence issuance is a logged-in surface; signed-out visitors land on
    // the public page (site-ID login), never a vendor form.
    const me = await meSession();
    if (!me) throw redirect({ to: "/" });
  },
  component: () => (
    <RequireAuth>
      <Licence />
    </RequireAuth>
  ),
});

const downloadRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/download",
  beforeLoad: async () => {
    const me = await meSession();
    if (!me) throw redirect({ to: "/" });
  },
  component: () => (
    <RequireAuth>
      <Downloads />
    </RequireAuth>
  ),
});

// Bare layout, mirroring src/routes/admin.tsx — the page is the index child
// and the vendor login is the /login child.
const adminRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin",
  component: () => <Outlet />,
});

const adminIndexRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/",
  // Unauthenticated visitors never see the vendor area at all — they bounce
  // to the public landing. Vendor login stays at the unlinked /admin/login.
  beforeLoad: async () => {
    const me = await meSession();
    if (!me) throw redirect({ to: "/" });
  },
  component: () => (
    <RequireAdmin>
      <Admin />
    </RequireAdmin>
  ),
});

const adminLoginRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/login",
  validateSearch: (s: Record<string, unknown>): { redirect?: string } => ({
    redirect: typeof s.redirect === "string" ? s.redirect : undefined,
  }),
  // Already-signed-in vendor staff have nothing to do on the login form.
  beforeLoad: async () => {
    const me = await meSession();
    if (me?.role === "vendor_admin") throw redirect({ to: "/admin" });
  },
  component: AdminLogin,
});

const adminRouteWithChildren = adminRoute.addChildren([adminIndexRoute, adminLoginRoute]);

const routeTree = rootRoute.addChildren([
  landingRoute,
  siteRouteWithChildren,
  siteLoginRoute,
  loginRoute,
  licenceRoute,
  downloadRoute,
  adminRouteWithChildren,
]);

export function getRouter() {
  return createRouter({ routeTree });
}

export { Link };
