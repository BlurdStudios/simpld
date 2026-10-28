import { createRootRoute, Outlet } from "@tanstack/react-router";
import { AuthProvider } from "@/lib/session";
import { DashboardShell } from "@/components/shell";

export const Route = createRootRoute({
  component: () => (
    <AuthProvider>
      <DashboardShell>
        <Outlet />
      </DashboardShell>
    </AuthProvider>
  ),
});
