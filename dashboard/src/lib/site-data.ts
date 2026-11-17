// Shared data plumbing for the customer portal pages (/s/:siteId/*).
// All reader pages use the same session-only endpoints and the same 15s
// AbortController timeout, so a hung backend ends in the error card instead
// of an eternal skeleton.

import { useEffect, useState } from "react";
import type { LicenceLogRow } from "./db";
import type { ReleaseManifest, SiteSnapshot } from "./contract";

// The boneyard CLI sets this before page load so `<BoneSkeleton fixture>`
// renders mock content while bones are captured. Never true in production.
export const isBuild =
  typeof window !== "undefined" &&
  (window as unknown as { __BONEYARD_BUILD?: boolean }).__BONEYARD_BUILD === true;

// Shared boneyard look: shadcn muted fill, shimmer, viewport-keyed breakpoints
// (the shell's content column is narrower than the window).
export const boneStyle = {
  animate: "shimmer",
  color: "hsl(var(--muted))",
  darkColor: "hsl(var(--muted))",
  select: "viewport",
} as const;

export const TIMEOUT_MS = 15_000;

export type LoadState<T> =
  | { status: "loading" }
  | { status: "ready"; data: T; mock: boolean }
  | { status: "empty"; message: string }
  | { status: "error"; message: string }
  | { status: "unauthenticated" }
  | { status: "forbidden" };

// What the shared fetcher returns: the LoadState variants plus a network
// failure marker, which only the snapshot hook turns into dev mock data.
type FetchResult =
  | { status: "ready"; data: unknown }
  | { status: "empty"; message: string }
  | { status: "error"; message: string }
  | { status: "unauthenticated" }
  | { status: "forbidden" }
  | { status: "network-error" };

/** One fetch with a hard deadline. HTTP status is mapped here, once: a 401 is
 *  an auth gate, a 403 is restricted content; timeouts and 5xx stay errors.
 *  A network error is told apart via `controller.signal.aborted` so the caller
 *  can keep the dev-mock fallback for a missing backend. */
async function fetchSiteJson(
  url: string,
  { label, emptyMessage }: { label: string; emptyMessage?: string }
): Promise<FetchResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (res.status === 401) return { status: "unauthenticated" };
    if (res.status === 403) return { status: "forbidden" };
    if (res.status === 404 && emptyMessage) return { status: "empty", message: emptyMessage };
    if (!res.ok) return { status: "error", message: `${label} failed (${res.status}).` };
    const data = await res.json().catch(() => null);
    return { status: "ready", data };
  } catch {
    return controller.signal.aborted
      ? { status: "error", message: `Request timed out after ${TIMEOUT_MS / 1000} seconds.` }
      : { status: "network-error" };
  } finally {
    clearTimeout(timer);
  }
}

// ---------- snapshot fixtures (Overview / Production / Orders / Stock) ----------

export function mockSnapshot(siteId: string): SiteSnapshot {
  const days: Record<string, SiteSnapshot["days"][string]> = {};
  const mkReel = (i: number, job: string, net: number) => ({
    roll_code: `RC-${i.toString().padStart(4, "0")}`,
    roll_label: `Roll ${i}`,
    job_id: job,
    net_kg: net,
  });
  const mkBox = (i: number, job: string, gross: number, rolls: string[]) => ({
    carton_no: `CT-${i.toString().padStart(4, "0")}`,
    bundle_no: `B-${Math.ceil(i / 4)}`,
    job_id: job,
    num_reels: rolls.length,
    total_gross_kg: gross,
    linked_rolls: rolls,
  });
  const jobs = ["J-1042", "J-1043", "J-1044"];
  for (let d = 0; d < 14; d++) {
    const day = new Date(Date.now() - d * 86400000).toISOString().slice(0, 10);
    const reelRows = [];
    const boxRows = [];
    let reelNet = 0;
    let boxGross = 0;
    for (let i = 0; i < 8 + (d % 5); i++) {
      const job = jobs[i % jobs.length];
      const net = 12 + (i % 6) * 3 + d;
      reelRows.push(mkReel(d * 100 + i + 1, job, net));
      reelNet += net;
    }
    for (let i = 0; i < 3 + (d % 3); i++) {
      const job = jobs[i % jobs.length];
      const rolls = reelRows.slice(i * 2, i * 2 + 2).map((r) => r.roll_code);
      const gross = 24 + i * 7 + d;
      boxRows.push(mkBox(d * 100 + i + 1, job, gross, rolls));
      boxGross += gross;
    }
    const byJob: Record<string, { reels: number; reel_net_kg: number; boxes: number; box_gross_kg: number }> = {};
    for (const j of jobs) {
      const jr = reelRows.filter((r) => r.job_id === j);
      const jb = boxRows.filter((b) => b.job_id === j);
      byJob[j] = {
        reels: jr.length,
        reel_net_kg: jr.reduce((a, r) => a + r.net_kg, 0),
        boxes: jb.length,
        box_gross_kg: jb.reduce((a, b) => a + b.total_gross_kg, 0),
      };
    }
    days[day] = {
      day,
      reel_rows: reelRows,
      box_rows: boxRows,
      totals: {
        reel_count: reelRows.length,
        reel_net_kg: reelNet,
        box_count: boxRows.length,
        box_gross_kg: boxGross,
      },
      by_job: byJob,
    };
  }
  return {
    site_id: siteId,
    pushed_at: new Date().toISOString(),
    days,
    orders: [
      { order_id: "ORD-2041", buyer: "Acme Corp", target_qty: 5000, loaded_qty: 3820, status: "in_progress" },
      { order_id: "ORD-2042", buyer: "Globex", target_qty: 2000, loaded_qty: 2000, status: "complete" },
      { order_id: "ORD-2043", buyer: "Initech", target_qty: 3500, loaded_qty: 900, status: "in_progress" },
      { order_id: "ORD-2044", buyer: "Umbrella", target_qty: 1200, loaded_qty: 0, status: "pending" },
    ],
    stock_counts: { raw_rolls: 47, packed_cartons: 18, dispatched_cartons: 62 },
  };
}

/** Shared reader for GET /api/s/:siteId. On a missing backend (`vite dev`
 *  without the Worker) it keeps the old dev-mock badge behaviour: mock data
 *  is only ever used when the network call itself fails, never on an HTTP
 *  error and never on a timeout. */
export function useSiteSnapshot(siteId: string): LoadState<SiteSnapshot> {
  const [state, setState] = useState<LoadState<SiteSnapshot>>({ status: "loading" });

  useEffect(() => {
    // Capture runs on the fixture — no fetch, so a 401/error card cannot
    // unmount the <BoneSkeleton> before boneyard snapshots it.
    if (isBuild) return;
    let live = true;
    setState({ status: "loading" });
    (async () => {
      const res = await fetchSiteJson(`/api/s/${encodeURIComponent(siteId)}`, {
        label: "Viewer request",
        emptyMessage: "Your figures will appear here after the first sync from the factory.",
      });
      if (!live) return;
      if (res.status === "network-error") {
        // Dev fallback: no Worker backend under `vite dev` — show mock shape.
        setState({ status: "ready", data: mockSnapshot(siteId), mock: true });
        return;
      }
      if (res.status === "ready") {
        setState({ status: "ready", data: res.data as SiteSnapshot, mock: false });
        return;
      }
      setState(res);
    })();
    return () => {
      live = false;
    };
  }, [siteId]);

  return state;
}

// ---------- licence fixtures / hook ----------

export function mockLicences(siteId: string): LicenceLogRow[] {
  const base = {
    site_id: siteId,
    customer: "Acme Foods",
    packager_fp: "9f2c1a7b4e8d0c63",
    max_stations: 3,
    issued_by: "ops@vendor.example",
  };
  return [
    {
      ...base,
      id: "lic-1",
      serial: "SIMPLD-20260203-ab12",
      edition: "pro",
      issued: "2026-02-03",
      expires: "2027-06-30",
      max_version: "2.x",
      created_at: "2026-02-03T11:20:00.000Z",
    },
    {
      ...base,
      id: "lic-2",
      serial: "SIMPLD-20251120-cd34",
      edition: "simple",
      issued: "2025-11-20",
      expires: "2026-11-20",
      max_version: "2.x",
      created_at: "2025-11-20T08:10:00.000Z",
    },
  ];
}

/** GET /api/licenses (session). The API already scopes clients to their own
 *  site; filtering again keeps a vendor admin's preview of another site
 *  honest. */
export function useSiteLicences(siteId: string): LoadState<LicenceLogRow[]> {
  const [state, setState] = useState<LoadState<LicenceLogRow[]>>({ status: "loading" });

  useEffect(() => {
    if (isBuild) return;
    let live = true;
    setState({ status: "loading" });
    (async () => {
      const res = await fetchSiteJson("/api/licenses", { label: "Licence request" });
      if (!live) return;
      if (res.status === "network-error") {
        setState({ status: "error", message: "Licence request failed." });
        return;
      }
      if (res.status !== "ready") {
        setState(res);
        return;
      }
      const raw = (res.data as { licences?: unknown } | null)?.licences;
      const rows = Array.isArray(raw) ? (raw as LicenceLogRow[]) : [];
      setState({
        status: "ready",
        data: rows.filter((l) => l?.site_id === siteId),
        mock: false,
      });
    })();
    return () => {
      live = false;
    };
  }, [siteId]);

  return state;
}

// ---------- downloads fixtures / hook ----------

export const MOCK_RELEASES: ReleaseManifest[] = [
  {
    version: "2.4.1",
    file: "Simpld-Setup-2.4.1.exe",
    sha256: "a1b2c3d4e5f60718293a4b5c6d7e8f900123456789abcdef0123456789abcdef0",
    notes: "Faster sync, clearer licence warnings.",
  },
  {
    version: "2.4.0",
    file: "Simpld-Setup-2.4.0.exe",
    sha256: "0fedcba9876543210fedcba9876543210fedcba9876543210fedcba987654321",
    notes: "Reporter fingerprints and station heartbeats.",
  },
];

/** GET /api/releases (session). */
export function useSiteReleases(): LoadState<{ releases: ReleaseManifest[]; latest: ReleaseManifest | null }> {
  const [state, setState] = useState<LoadState<{ releases: ReleaseManifest[]; latest: ReleaseManifest | null }>>({
    status: "loading",
  });

  useEffect(() => {
    if (isBuild) return;
    let live = true;
    setState({ status: "loading" });
    (async () => {
      const res = await fetchSiteJson("/api/releases", { label: "Releases request" });
      if (!live) return;
      if (res.status === "network-error") {
        setState({ status: "error", message: "Releases request failed." });
        return;
      }
      if (res.status !== "ready") {
        setState(res);
        return;
      }
      const data = (res.data ?? {}) as { releases?: unknown; latest?: unknown };
      setState({
        status: "ready",
        data: {
          releases: Array.isArray(data.releases) ? (data.releases as ReleaseManifest[]) : [],
          latest: (data.latest as ReleaseManifest | null) ?? null,
        },
        mock: false,
      });
    })();
    return () => {
      live = false;
    };
  }, []);

  return state;
}
