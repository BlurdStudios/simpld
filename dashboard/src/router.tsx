// Code-based router (works with or without the TanStack codegen plugin).
// File-based routes in src/routes/* remain the canonical layout: when
// @tanstack/router-plugin runs, it regenerates routeTree.gen.ts and main.tsx
// prefers it. This fallback keeps `npm run dev` working offline/from scratch.
import {
  Link,
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  useNavigate,
  useParams,
  useSearch,
} from "@tanstack/react-router";
import { AuthProvider, RequireAdmin, RequireAuth } from "@/lib/session";
import { DashboardShell } from "@/components/shell";
import { Landing } from "./routes/index";
import { SiteViewer } from "./routes/s.$siteId";
import { Login } from "./routes/login";
import { Licence } from "./routes/licence";
import { Downloads } from "./routes/download";
import { Admin } from "./routes/admin";

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

const siteRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/s/$siteId",
  validateSearch: (s: Record<string, unknown>) => ({ key: (s.key as string) ?? "" }),
  component: () => {
    const { siteId } = useParams({ from: siteRoute.id });
    const { key } = useSearch({ from: siteRoute.id });
    return <SiteViewer siteId={siteId} viewerKey={key} />;
  },
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  validateSearch: (s: Record<string, unknown>) => ({
    redirect: (s.redirect as string) ?? "/",
  }),
  component: Login,
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

const adminRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin",
  component: () => (
    <RequireAdmin>
      <Admin />
    </RequireAdmin>
  ),
});

const routeTree = rootRoute.addChildren([
  landingRoute,
  siteRoute,
  loginRoute,
  licenceRoute,
  downloadRoute,
  adminRoute,
]);

export function getRouter() {
  return createRouter({ routeTree });
}

export { Link };
