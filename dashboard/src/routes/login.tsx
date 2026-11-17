import { createFileRoute, Navigate } from "@tanstack/react-router";

// Legacy entry point — the vendor form now lives at /admin/login.
export const Route = createFileRoute("/login")({
  component: () => <Navigate to="/admin/login" replace />,
});
