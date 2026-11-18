import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

export interface Me {
  id: string;
  email: string;
  role: "vendor_admin" | "client";
  site_id: string | null;
}

interface AuthState {
  me: Me | null | undefined;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  me: undefined,
  refresh: async () => {},
  logout: async () => {},
});

// Module-level session promise: ONE /api/auth/me round-trip shared by the
// AuthProvider and every router beforeLoad guard. Guards resolve from cache
// after the first load — no duplicate fetch, no flash of a wrong redirect.
let mePromise: Promise<Me | null> | null = null;

export function meSession(): Promise<Me | null> {
  if (!mePromise) {
    mePromise = fetch("/api/auth/me")
      .then((res) => (res.ok ? res.json() : null))
      .then((data: { user?: Me } | null) => data?.user ?? null)
      .catch(() => null);
  }
  return mePromise;
}

/** Forget the cached session (after login/logout) so the next guard re-checks. */
export function resetSession(): void {
  mePromise = null;
}

/** Where a signed-out user of this role should land. Site users go to THEIR
 * site login — the vendor login is never a destination for them. */
export function loginDest(me: Me | null): string {
  return me?.role === "client" && me.site_id ? `/s/${me.site_id}/login` : "/admin/login";
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/auth/me");
      const data = res.ok ? await res.json() : null;
      const user = (data?.user ?? null) as Me | null;
      mePromise = Promise.resolve(user);
      setMe(user);
    } catch {
      mePromise = Promise.resolve(null);
      setMe(null);
    }
  }, []);

  const logout = useCallback(async () => {
    // Role-aware exit: site users land on THEIR site login; vendor staff on
    // the vendor login (which site users never see).
    const dest = loginDest(me ?? null);
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } finally {
      resetSession();
      setMe(null);
      window.location.href = dest;
    }
  }, [me]);

  useEffect(() => {
    let live = true;
    (async () => {
      const user = await meSession();
      if (live) setMe(user);
    })();
    return () => {
      live = false;
    };
  }, []);

  const value = useMemo(() => ({ me, refresh, logout }), [me, refresh, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

/** Back-compat for pages written against the old standalone hook. */
export function useMe(): Me | null | undefined {
  return useAuth().me;
}

export async function logout(): Promise<void> {
  const me = await meSession();
  await fetch("/api/auth/logout", { method: "POST" });
  resetSession();
  window.location.href = loginDest(me);
}

/** Shared 403 card for protected pages that must never redirect to a login. */
export function Restricted({ detail }: { detail?: string }) {
  return (
    <div className="flex min-h-[40vh] flex-col items-center justify-center gap-2 text-center">
      <p className="text-4xl font-bold">403</p>
      <p className="text-lg font-semibold">Restricted content</p>
      <p className="text-sm text-muted-foreground">Please contact your administrator.</p>
      {detail && <p className="text-sm text-muted-foreground">{detail}</p>}
    </div>
  );
}

/** Render children only when logged in; otherwise show the shared 403. */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { me } = useAuth();
  if (me === undefined) return null;
  if (me === null) return <Restricted />;
  return <>{children}</>;
}

/** Render children only for vendor_admin; everyone else gets the shared 403. */
export function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { me } = useAuth();
  if (me === undefined) return null;
  if (me === null) return <Restricted />;
  if (me.role !== "vendor_admin") {
    return <Restricted detail={`Vendor admin only — signed in as ${me.email}.`} />;
  }
  return <>{children}</>;
}
