import { Outlet, createFileRoute } from "@tanstack/react-router";

// Customer portal layout. Each /s/:siteId/* page is a real route; the index
// route renders the Overview. Session access is enforced by the API calls on
// every page (401 -> this site's login, 403 -> restricted card).
export const Route = createFileRoute("/s/$siteId")({
  component: () => <Outlet />,
});
