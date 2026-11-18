import { createFileRoute, Navigate } from "@tanstack/react-router";

// Legacy entry point — site users get the landing page (site-ID login);
// the vendor login is never linked publicly (vendor staff: /admin/login).
export const Route = createFileRoute("/login")({
  component: () => <Navigate to="/" replace />,
});
