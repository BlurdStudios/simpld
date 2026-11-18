import { useState } from "react";
import { Navigate, createFileRoute, useNavigate, useParams } from "@tanstack/react-router";
import { HugeiconsIcon } from "@hugeicons/react";
import { Login01Icon } from "@hugeicons/core-free-icons";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/session";

export const Route = createFileRoute("/s/$siteId/login")({
  component: SiteLogin,
});

export function SiteLogin() {
  const { siteId } = useParams({ from: "/s/$siteId/login" });
  const navigate = useNavigate();
  const { me, refresh } = useAuth();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Already signed in with access to this site — no need to log in again.
  if (me === undefined) return null;
  if (me && (me.role === "vendor_admin" || me.site_id === siteId))
    return <Navigate to="/s/$siteId" params={{ siteId }} replace />;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site_id: siteId, password }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.error ?? `login failed (${res.status})`);
      await refresh();
      navigate({ to: "/s/$siteId", params: { siteId } });
    } catch (err) {
      setError(err instanceof Error ? err.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  // Layman-simple: ONE card, password only. The vendor admin sets and shares
  // passwords; there are no one-time codes or first-login flows.
  return (
    <div className="mx-auto flex min-h-[60vh] max-w-md flex-col justify-center gap-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <HugeiconsIcon icon={Login01Icon} size={20} /> Customer login
          </CardTitle>
          <CardDescription>Site: {siteId}</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="login-password">Password</Label>
              <Input
                id="login-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            {error && <p className="text-sm font-medium text-destructive">{error}</p>}
            <Button disabled={busy || !password}>Log in</Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
