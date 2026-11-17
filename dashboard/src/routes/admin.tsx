import { Outlet, createFileRoute } from "@tanstack/react-router";

// Bare admin layout: the dashboard lives in admin.index.tsx and the vendor
// login in admin.login.tsx. Both render through this Outlet.
export const Route = createFileRoute("/admin")({
  component: () => <Outlet />,
});
