import { useState } from "react";
import { createFileRoute, useNavigate, useSearch } from "@tanstack/react-router";
import { HugeiconsIcon } from "@hugeicons/react";
import { Login01Icon, Key01Icon } from "@hugeicons/core-free-icons";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/session";

export const Route = createFileRoute("/login")({
  validateSearch: (s: Record<string, unknown>) => ({
    redirect: (s.redirect as string) ?? "/",
  }),
  component: LoginRoute,
});

function LoginRoute() {
  return <Login />;
}

export function Login() {
  const navigate = useNavigate();
  const { redirect } = useSearch({ from: "/login" });
  const { refresh } = useAuth();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const id = identifier.trim();
      const body = id.includes("@") ? { email: id, password } : { site_id: id, password };
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.error ?? `login failed (${res.status})`);
      await refresh();
      navigate({ to: redirect.startsWith("/") ? redirect : "/" });
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
            <HugeiconsIcon icon={Login01Icon} size={20} /> Vendor portal login
          </CardTitle>
          <CardDescription>
            Clients log in with their Site ID; vendor admins with email. No self-registration.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="login-id">Site ID or email</Label>
              <Input
                id="login-id"
                autoComplete="username"
                placeholder="e.g. ram-vendor"
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
              />
            </div>
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
            <Button disabled={busy || !identifier || !password}>Log in</Button>
          </form>
        </CardContent>
      </Card>
      <FirstSetup />
    </div>
  );
}

function FirstSetup() {
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [siteId, setSiteId] = useState("");
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
        body: JSON.stringify({ site_id: siteId.trim(), serial: serial.trim(), code: code.trim(), password }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.error ?? `setup failed (${res.status})`);
      await refresh();
      navigate({ to: `/s/$siteId`, params: { siteId: siteId.trim() } });
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
            <Label htmlFor="setup-site">Site ID</Label>
            <Input id="setup-site" value={siteId} onChange={(e) => setSiteId(e.target.value)} />
          </div>
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
          <Button disabled={busy || !siteId.trim() || !serial.trim() || !code.trim() || password.length < 8}>
            Set password + log in
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
