import { useEffect, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { HugeiconsIcon } from "@hugeicons/react";
import {
  UserIcon,
  Key01Icon,
  WarehouseIcon,
  File01Icon,
  Download01Icon,
  PlusSignIcon,
  Cancel01Icon,
} from "@hugeicons/core-free-icons";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { RequireAdmin } from "@/lib/session";

export const Route = createFileRoute("/admin")({
  component: () => (
    <RequireAdmin>
      <Admin />
    </RequireAdmin>
  ),
});

async function api(path: string, init?: RequestInit) {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.error ?? `${path} failed (${res.status})`);
  return data;
}

interface AdminUser {
  id: string;
  email: string;
  role: string;
  site_id: string | null;
  disabled: boolean;
  created_at: string;
}

interface AdminKey {
  id: string;
  user_id: string;
  site_id: string;
  label: string;
  created_at: string;
  revoked_at: string | null;
}

interface AdminSite {
  site_id: string;
  last_push_at: string | null;
  last_reporter_fp: string | null;
  frozen: boolean;
  active_keys: number;
  reporters_seen: number;
}

const TABS = [
  { id: "users", label: "Users", icon: UserIcon },
  { id: "keys", label: "API keys", icon: Key01Icon },
  { id: "sites", label: "Sites", icon: WarehouseIcon },
  { id: "licences", label: "Licences", icon: File01Icon },
  { id: "releases", label: "Releases", icon: Download01Icon },
] as const;

export function Admin() {
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("users");
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Admin</h1>
        <p className="text-sm text-muted-foreground">
          God mode: users, keys, sites, licences, releases. Actions log under the issuing admin.
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        {TABS.map((t) => (
          <Button
            key={t.id}
            variant={tab === t.id ? "default" : "outline"}
            size="sm"
            onClick={() => {
              setTab(t.id);
              setError(null);
            }}
          >
            <HugeiconsIcon icon={t.icon} size={16} /> {t.label}
          </Button>
        ))}
      </div>
      {error && (
        <p className="rounded-md border border-destructive/50 bg-destructive/10 px-4 py-2 text-sm font-medium text-destructive">
          {error}
        </p>
      )}
      {tab === "users" && <UsersTab onError={setError} />}
      {tab === "keys" && <KeysTab onError={setError} />}
      {tab === "sites" && <SitesTab onError={setError} />}
      {tab === "licences" && <LicencesTab onError={setError} />}
      {tab === "releases" && <ReleasesTab onError={setError} />}
    </div>
  );
}

function UsersTab({ onError }: { onError: (e: string | null) => void }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("client");
  const [siteId, setSiteId] = useState("");

  async function refresh() {
    try {
      const d = await api("/api/admin/users");
      setUsers(d.users);
      onError(null);
    } catch (e) {
      onError(e instanceof Error ? e.message : "users failed");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api("/api/admin/users", {
        method: "POST",
        body: JSON.stringify({ email, password, role, site_id: siteId || null }),
      });
      setEmail("");
      setPassword("");
      setSiteId("");
      await refresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : "create failed");
    }
  }

  async function patch(id: string, body: Record<string, unknown>) {
    try {
      await api(`/api/admin/users/${id}`, { method: "POST", body: JSON.stringify(body) });
      await refresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : "update failed");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <HugeiconsIcon icon={UserIcon} size={18} /> Users
        </CardTitle>
        <CardDescription>Vendor-provisioned. Client logins bind to one site.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <form onSubmit={create} className="flex flex-wrap items-end gap-2">
          <div className="grid gap-2">
            <Label htmlFor="u-email">Email</Label>
            <Input id="u-email" className="w-52" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="u-pass">Password</Label>
            <Input id="u-pass" className="w-40" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          <div className="grid gap-2">
            <Label>Role</Label>
            <select
              className="flex h-9 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
              value={role}
              onChange={(e) => setRole(e.target.value)}
            >
              <option value="client">client</option>
              <option value="vendor_admin">vendor_admin</option>
            </select>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="u-site">Site (clients)</Label>
            <Input id="u-site" className="w-40" placeholder="vasavi-f1" value={siteId} onChange={(e) => setSiteId(e.target.value)} />
          </div>
          <Button disabled={!email || !password}>
            <HugeiconsIcon icon={PlusSignIcon} size={16} /> Create
          </Button>
        </form>
        {loading ? (
          <Skeleton className="h-40" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Site</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((u) => (
                <TableRow key={u.id}>
                  <TableCell className="font-medium">{u.email}</TableCell>
                  <TableCell>
                    <Badge variant={u.role === "vendor_admin" ? "default" : "secondary"}>{u.role}</Badge>
                  </TableCell>
                  <TableCell>{u.site_id ?? "—"}</TableCell>
                  <TableCell>
                    <Badge variant={u.disabled ? "destructive" : "outline"}>
                      {u.disabled ? "disabled" : "active"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    <Button variant="outline" size="sm" onClick={() => patch(u.id, { disabled: !u.disabled })}>
                      {u.disabled ? "Enable" : "Disable"}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {users.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="text-muted-foreground">No users yet.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function KeysTab({ onError }: { onError: (e: string | null) => void }) {
  const [keys, setKeys] = useState<AdminKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [userId, setUserId] = useState("");
  const [siteId, setSiteId] = useState("");
  const [label, setLabel] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [showOnce, setShowOnce] = useState<string | null>(null);

  async function refresh() {
    try {
      const d = await api("/api/admin/keys");
      setKeys(d.keys);
      onError(null);
    } catch (e) {
      onError(e instanceof Error ? e.message : "keys failed");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      const d = await api("/api/admin/keys", {
        method: "POST",
        body: JSON.stringify({ user_id: userId, site_id: siteId, label, confirm }),
      });
      setShowOnce(d.key);
      setConfirm(false);
      await refresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : "create failed");
    }
  }

  async function revoke(id: string) {
    if (!window.confirm(`Revoke key ${id}? Pushes and viewer access with it start failing 401.`)) return;
    try {
      await api(`/api/admin/keys/${id}/revoke`, { method: "POST", body: "{}" });
      await refresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : "revoke failed");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <HugeiconsIcon icon={Key01Icon} size={18} /> API keys
        </CardTitle>
        <CardDescription>One active key per user — creating one revokes the old. Only hashes are stored.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <form onSubmit={create} className="flex flex-wrap items-end gap-2">
          <div className="grid gap-2">
            <Label htmlFor="k-user">User ID</Label>
            <Input id="k-user" className="w-40" value={userId} onChange={(e) => setUserId(e.target.value)} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="k-site">Site ID</Label>
            <Input id="k-site" className="w-40" value={siteId} onChange={(e) => setSiteId(e.target.value)} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="k-label">Label</Label>
            <Input id="k-label" className="w-40" value={label} onChange={(e) => setLabel(e.target.value)} />
          </div>
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
            revoke old keys
          </label>
          <Button disabled={!userId || !siteId}>
            <HugeiconsIcon icon={PlusSignIcon} size={16} /> Create
          </Button>
        </form>
        {showOnce && (
          <div className="grid gap-2 rounded-lg border bg-muted p-4">
            <p className="text-sm font-medium">New key — shown once, copy now:</p>
            <code className="break-all text-xs">{showOnce}</code>
            <div>
              <Button
                size="sm"
                variant="outline"
                onClick={() => navigator.clipboard?.writeText(showOnce).catch(() => {})}
              >
                Copy
              </Button>
            </div>
          </div>
        )}
        {loading ? (
          <Skeleton className="h-40" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>ID</TableHead>
                <TableHead>User</TableHead>
                <TableHead>Site</TableHead>
                <TableHead>Label</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {keys.map((k) => (
                <TableRow key={k.id}>
                  <TableCell className="font-mono text-xs">{k.id}</TableCell>
                  <TableCell className="font-mono text-xs">{k.user_id}</TableCell>
                  <TableCell>{k.site_id}</TableCell>
                  <TableCell>{k.label}</TableCell>
                  <TableCell>
                    <Badge variant={k.revoked_at ? "destructive" : "outline"}>
                      {k.revoked_at ? `revoked ${k.revoked_at}` : "active"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    {!k.revoked_at && (
                      <Button variant="outline" size="sm" onClick={() => revoke(k.id)}>
                        <HugeiconsIcon icon={Cancel01Icon} size={14} /> Revoke
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {keys.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="text-muted-foreground">No keys yet.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function SitesTab({ onError }: { onError: (e: string | null) => void }) {
  const [sites, setSites] = useState<AdminSite[]>([]);
  const [loading, setLoading] = useState(true);
  const [reporters, setReporters] = useState<Record<string, Array<{ station_id: string; packager_fp: string; seen_at: string }>>>({});

  async function refresh() {
    try {
      const d = await api("/api/admin/sites");
      setSites(d.sites);
      onError(null);
    } catch (e) {
      onError(e instanceof Error ? e.message : "sites failed");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function toggleFreeze(s: AdminSite) {
    const action = s.frozen ? "unfreeze" : "freeze";
    if (!s.frozen && !window.confirm(`Freeze ${s.site_id}? ALL its keys are revoked (pushes + viewer 401).`)) return;
    try {
      await api(`/api/admin/sites/${encodeURIComponent(s.site_id)}/${action}`, { method: "POST", body: "{}" });
      await refresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : "freeze failed");
    }
  }

  async function loadReporters(siteId: string) {
    try {
      const d = await api(`/api/admin/sites/${encodeURIComponent(siteId)}/reporters`);
      setReporters((r) => ({ ...r, [siteId]: d.reporters }));
    } catch (err) {
      onError(err instanceof Error ? err.message : "reporters failed");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <HugeiconsIcon icon={WarehouseIcon} size={18} /> Sites
        </CardTitle>
        <CardDescription>Last push, reporter fingerprints, freeze control.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {loading ? (
          <Skeleton className="h-40" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Site</TableHead>
                <TableHead>Last push</TableHead>
                <TableHead>Reporter fp</TableHead>
                <TableHead>Keys</TableHead>
                <TableHead>Reporters</TableHead>
                <TableHead>Frozen</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sites.map((s) => (
                <TableRow key={s.site_id}>
                  <TableCell className="font-medium">{s.site_id}</TableCell>
                  <TableCell>{s.last_push_at ?? "—"}</TableCell>
                  <TableCell className="max-w-40 truncate font-mono text-xs">{s.last_reporter_fp ?? "—"}</TableCell>
                  <TableCell>{s.active_keys}</TableCell>
                  <TableCell>{s.reporters_seen}</TableCell>
                  <TableCell>
                    <Badge variant={s.frozen ? "destructive" : "outline"}>{s.frozen ? "frozen" : "live"}</Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button variant="outline" size="sm" onClick={() => toggleFreeze(s)}>
                        {s.frozen ? "Unfreeze" : "Freeze"}
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => loadReporters(s.site_id)}>
                        Reporters
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {sites.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="text-muted-foreground">No sites pushed yet.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        )}
        {Object.entries(reporters).map(([site, rows]) => (
          <div key={site} className="rounded-lg border bg-muted p-4 text-sm">
            <p className="font-medium">{site} — {rows.length} sightings</p>
            {rows.slice(0, 10).map((r, i) => (
              <p key={i} className="break-all font-mono text-xs text-muted-foreground">
                {r.seen_at} · station {r.station_id || "—"} · fp {r.packager_fp || "—"}
              </p>
            ))}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function LicencesTab({ onError }: { onError: (e: string | null) => void }) {
  const [rows, setRows] = useState<
    Array<{ serial: string; site_id: string; edition: string; packager_fp: string; max_stations: number; expires: string; issued_by: string; created_at: string }>
  >([]);
  const [tracked, setTracked] = useState<
    Array<{ site_id: string; edition: string; expires: string; days_left: number | null; state: string; app_version: string; packager_fp: string; updated_at: string }>
  >([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    (async () => {
      try {
        const d = await api("/api/licenses");
        setRows(d.licences);
        const t = await api("/api/admin/site-licences");
        setTracked(t.tracked ?? []);
        onError(null);
      } catch (e) {
        onError(e instanceof Error ? e.message : "licences failed");
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const trackedSites = new Set(tracked.map((t) => t.site_id));
  const neverActivated = rows
    .filter((l) => !trackedSites.has(l.site_id))
    .filter((l, i, a) => a.findIndex((x) => x.site_id === l.site_id) === i);
  return (
    <div className="grid gap-6">
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <HugeiconsIcon icon={File01Icon} size={18} /> Live tracking — who needs attention
        </CardTitle>
        <CardDescription>
          Pushed by each factory on sync: live edition, countdown and app version.
          Issued-but-never-pushed rows never activated their key — chase those first.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-40" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Site</TableHead>
                <TableHead>Live edition</TableHead>
                <TableHead>Expires</TableHead>
                <TableHead>Left</TableHead>
                <TableHead>State</TableHead>
                <TableHead>App</TableHead>
                <TableHead>Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tracked.map((t) => {
                const flag = flagFor(t);
                return (
                  <TableRow key={t.site_id}>
                    <TableCell className="font-medium">{t.site_id}</TableCell>
                    <TableCell>
                      <Badge variant="secondary">{t.edition || "—"}</Badge>
                    </TableCell>
                    <TableCell>{t.expires || "—"}</TableCell>
                    <TableCell>{t.days_left == null ? "—" : `${t.days_left}d`}</TableCell>
                    <TableCell>{t.state || "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{t.app_version || "—"}</TableCell>
                    <TableCell>
                      {flag ? (
                        <Badge variant={flag.tone === "err" ? "destructive" : "outline"}>{flag.label}</Badge>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
              {neverActivated.map((l) => (
                <TableRow key={"na-" + l.site_id}>
                  <TableCell className="font-medium">{l.site_id}</TableCell>
                  <TableCell colSpan={5} className="text-muted-foreground">
                    Key issued {l.issued_by ? `by ${l.issued_by}` : ""} — never activated (no push yet)
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">Chase activation</Badge>
                  </TableCell>
                </TableRow>
              ))}
              {tracked.length === 0 && neverActivated.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="text-muted-foreground">No licences issued yet.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <HugeiconsIcon icon={File01Icon} size={18} /> Licences issued
        </CardTitle>
        <CardDescription>Audit log of every signed licence.</CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-40" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Serial</TableHead>
                <TableHead>Site</TableHead>
                <TableHead>Edition</TableHead>
                <TableHead>Machines</TableHead>
                <TableHead>Expires</TableHead>
                <TableHead>Issued by</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((l) => (
                <TableRow key={l.serial + l.created_at}>
                  <TableCell className="font-medium">{l.serial}</TableCell>
                  <TableCell>{l.site_id}</TableCell>
                  <TableCell>
                    <Badge variant="secondary">{l.edition}</Badge>
                  </TableCell>
                  <TableCell>{l.max_stations}</TableCell>
                  <TableCell>{l.expires}</TableCell>
                  <TableCell className="text-muted-foreground">{l.issued_by}</TableCell>
                </TableRow>
              ))}
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="text-muted-foreground">None issued yet.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
    </div>
  );
}

function flagFor(t: { edition: string; days_left: number | null; state: string }): {
  label: string; tone: "warn" | "err";
} | null {
  if (t.edition === "trial") {
    if (t.days_left == null) return null;
    if (t.days_left < 0) return { label: "Trial over — pitch upgrade", tone: "err" };
    if (t.days_left <= 3) return { label: `Nudge — ${t.days_left}d left`, tone: "warn" };
    return null;
  }
  if (t.state === "blocked" || t.state === "grace") return { label: "OVERDUE — renew now", tone: "err" };
  if (t.state === "expiring") return { label: "Renew soon", tone: "warn" };
  return null;
}

function ReleasesTab({ onError }: { onError: (e: string | null) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [notes, setNotes] = useState("");
  const [force, setForce] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    const form = new FormData();
    form.set("file", file);
    form.set("notes", notes);
    if (force) form.set("force", "1");
    try {
      const res = await fetch("/api/admin/releases", { method: "POST", body: form });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.error ?? `upload failed (${res.status})`);
      setResult(`Published v${data.release.version} (${data.release.file}, sha256 ${data.release.sha256.slice(0, 16)}…)`);
      onError(null);
    } catch (err) {
      onError(err instanceof Error ? err.message : "upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <HugeiconsIcon icon={Download01Icon} size={18} /> Publish release
        </CardTitle>
        <CardDescription>
          Upload a <code>Simpld-Setup-X.Y.Z.exe</code> installer. Hashed (SHA-256),
          stored in R2, manifest updated. Downgrades refused unless forced.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={upload} className="grid max-w-lg gap-4">
          <Input type="file" accept=".exe" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <div className="grid gap-2">
            <Label htmlFor="rel-notes">Notes (shown in the update prompt)</Label>
            <Input id="rel-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </div>
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
            allow downgrade / republish
          </label>
          <div>
            <Button disabled={!file || busy}>Upload + publish</Button>
          </div>
        </form>
        {result && <p className="mt-4 text-sm text-muted-foreground">{result}</p>}
      </CardContent>
    </Card>
  );
}
