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
  useParams,
} from "@tanstack/react-router";
import { AuthProvider, RequireAdmin, RequireAuth } from "@/lib/session";
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
  component: () => <Navigate to="/admin/login" replace />,
});

const licenceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/licence",
  component: () => (
    <RequireAuth>
      <Licence />
    </RequireAuth>
  ),
});

const downloadRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/download",
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
