import { useState } from "react";
import { createFileRoute, useNavigate, useSearch } from "@tanstack/react-router";
import { HugeiconsIcon } from "@hugeicons/react";
import { Login01Icon } from "@hugeicons/core-free-icons";
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
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
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
    <div className="mx-auto flex min-h-[60vh] max-w-md flex-col justify-center">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <HugeiconsIcon icon={Login01Icon} size={20} /> Vendor portal login
          </CardTitle>
          <CardDescription>
            Vendor-provisioned accounts only — no self-registration. Client logins
            are bound to one site.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="login-email">Email</Label>
              <Input
                id="login-email"
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
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
            <Button disabled={busy || !email || !password}>Log in</Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
