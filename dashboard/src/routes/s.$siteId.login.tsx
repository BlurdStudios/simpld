import { useState } from "react";
import { Navigate, createFileRoute, useNavigate, useParams } from "@tanstack/react-router";
import { HugeiconsIcon } from "@hugeicons/react";
import { Login01Icon, Key01Icon } from "@hugeicons/core-free-icons";
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
      <FirstSetup siteId={siteId} />
    </div>
  );
}

function FirstSetup({ siteId }: { siteId: string }) {
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [serial, setSerial] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/auth/setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site_id: siteId, serial: serial.trim(), code: code.trim(), password }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.error ?? `setup failed (${res.status})`);
      await refresh();
      navigate({ to: "/s/$siteId", params: { siteId } });
    } catch (err) {
      setError(err instanceof Error ? err.message : "setup failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <HugeiconsIcon icon={Key01Icon} size={18} /> First login? Set your password
        </CardTitle>
        <CardDescription>
          Use the one-time setup code and licence serial your vendor admin gave you.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="setup-serial">Licence serial</Label>
            <Input id="setup-serial" placeholder="SIMPLD-…" value={serial} onChange={(e) => setSerial(e.target.value)} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="setup-code">One-time setup code</Label>
            <Input id="setup-code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="setup-password">New password (8+ characters)</Label>
            <Input
              id="setup-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {error && <p className="text-sm font-medium text-destructive">{error}</p>}
          <Button disabled={busy || !serial.trim() || !code.trim() || password.length < 8}>
            Set password + log in
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
