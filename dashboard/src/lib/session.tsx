import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";

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

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/auth/me");
      const data = res.ok ? await res.json() : null;
      setMe(data?.user ?? null);
    } catch {
      setMe(null);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } finally {
      setMe(null);
      window.location.href = "/admin/login";
    }
  }, []);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const res = await fetch("/api/auth/me");
        const data = res.ok ? await res.json() : null;
        if (live) setMe(data?.user ?? null);
      } catch {
        if (live) setMe(null);
      }
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
  await fetch("/api/auth/logout", { method: "POST" });
  window.location.href = "/admin/login";
}

/** Shared 403 card for protected pages that must never redirect to a login. */
export function Restricted({ detail }: { detail?: string }) {
  return (
    <div className="flex min-h-[40vh] flex-col items-center justify-center gap-2 text-center">
      <p className="text-4xl font-bold">403</p>
      <p className="text-lg font-semibold">Restricted content</p>
      <p className="text-sm text-muted-foreground">Please contact your administrator.</p>
      {detail && <p className="text-sm text-muted-foreground">{detail}</p>}
      <Link
        to="/admin/login"
        className="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground"
      >
        Vendor staff? Log in
      </Link>
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
