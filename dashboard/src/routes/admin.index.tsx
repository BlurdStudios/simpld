import { useEffect, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { HugeiconsIcon } from "@hugeicons/react";
import { Skeleton as BoneSkeleton } from "boneyard-js/react";
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
import { newPassword } from "@/lib/site-id";
import { Skeleton } from "@/components/ui/skeleton";
import { slugifySiteId } from "@/lib/site-id";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { RequireAdmin } from "@/lib/session";

export const Route = createFileRoute("/admin/")({
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

type LicenceRow = {
  serial: string;
  site_id: string;
  edition: string;
  packager_fp: string;
  max_stations: number;
  expires: string;
  issued_by: string;
  created_at: string;
};

type TrackedSite = {
  site_id: string;
  edition: string;
  expires: string;
  days_left: number | null;
  state: string;
  app_version: string;
  packager_fp: string;
  updated_at: string;
};

// boneyard capture flag + shared look (same as download/s.$siteId).
const isBuild =
  typeof window !== "undefined" &&
  (window as unknown as { __BONEYARD_BUILD?: boolean }).__BONEYARD_BUILD === true;

const boneStyle = {
  animate: "shimmer",
  color: "hsl(var(--muted))",
  darkColor: "hsl(var(--muted))",
  select: "viewport",
} as const;

// Fixture data — only rendered while `npx boneyard-js build` captures bones.
const MOCK_USERS: AdminUser[] = [
  {
    id: "u-1",
    email: "ops@vendor.example",
    role: "vendor_admin",
    site_id: null,
    disabled: false,
    created_at: "2026-01-12T09:30:00.000Z",
  },
  {
    id: "u-2",
    email: "buyer@acme.example",
    role: "client",
    site_id: "acme-f1",
    disabled: false,
    created_at: "2026-02-03T11:15:00.000Z",
  },
  {
    id: "u-3",
    email: "old@globex.example",
    role: "client",
    site_id: "globex-f2",
    disabled: true,
    created_at: "2025-11-20T08:00:00.000Z",
  },
];

const MOCK_KEYS: AdminKey[] = [
  {
    id: "key-1",
    user_id: "u-2",
    site_id: "acme-f1",
    label: "factory-1",
    created_at: "2026-02-04T10:00:00.000Z",
    revoked_at: null,
  },
  {
    id: "key-2",
    user_id: "u-3",
    site_id: "globex-f2",
    label: "old-packager",
    created_at: "2025-11-21T10:00:00.000Z",
    revoked_at: "2026-01-05T10:00:00.000Z",
  },
];

const MOCK_SITES: AdminSite[] = [
  {
    site_id: "acme-f1",
    last_push_at: "2026-11-16T06:45:00.000Z",
    last_reporter_fp: "9f2c1a7b4e8d0c63",
    frozen: false,
    active_keys: 1,
    reporters_seen: 3,
  },
  {
    site_id: "globex-f2",
    last_push_at: null,
    last_reporter_fp: null,
    frozen: true,
    active_keys: 0,
    reporters_seen: 0,
  },
];

const MOCK_TRACKED: TrackedSite[] = [
  {
    site_id: "acme-f1",
    edition: "pro",
    expires: "2027-06-30",
    days_left: 225,
    state: "active",
    app_version: "2.4.1",
    packager_fp: "9f2c1a7b4e8d0c63",
    updated_at: "2026-11-16T06:45:00.000Z",
  },
  {
    site_id: "initech-f3",
    edition: "trial",
    expires: "2026-11-18",
    days_left: 2,
    state: "trial",
    app_version: "2.3.9",
    packager_fp: "abc123def4567890",
    updated_at: "2026-11-15T18:20:00.000Z",
  },
];

const MOCK_LICENCES: LicenceRow[] = [
  {
    serial: "SIMPLD-20260203-ab12",
    site_id: "acme-f1",
    edition: "pro",
    packager_fp: "9f2c1a7b4e8d0c63",
    max_stations: 3,
    expires: "2027-06-30",
    issued_by: "ops@vendor.example",
    created_at: "2026-02-03T11:20:00.000Z",
  },
  {
    serial: "SIMPLD-20251120-cd34",
    site_id: "globex-f2",
    edition: "simple",
    packager_fp: "0a1b2c3d4e5f6789",
    max_stations: 1,
    expires: "2026-11-20",
    issued_by: "ops@vendor.example",
    created_at: "2025-11-20T08:10:00.000Z",
  },
];

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
      {/* In capture mode all sections mount at once so every named skeleton
          can be snapshotted in a single CLI visit. Runtime keeps one tab. */}
      {(isBuild || tab === "users") && <UsersTab onError={setError} />}
      {(isBuild || tab === "keys") && <KeysTab onError={setError} />}
      {(isBuild || tab === "sites") && <SitesTab onError={setError} />}
      {(isBuild || tab === "licences") && <LicencesTab onError={setError} />}
      {(isBuild || tab === "releases") && <ReleasesTab onError={setError} />}
    </div>
  );
}

// ---------- section tables (shared by real content and boneyard fixtures) ----------

function UsersTable({
  users,
  pwBusy,
  pwPanel,
  onPatch,
  onPasswordStart,
}: {
  users: AdminUser[];
  pwBusy: string | null;
  pwPanel: { user: AdminUser; value: string; done: boolean } | null;
  onPatch: (id: string, body: Record<string, unknown>) => void;
  onPasswordStart: (u: AdminUser) => void;
}) {
  return (
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
              <div className="flex justify-end gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={pwBusy === u.id}
                  onClick={() => onPasswordStart(u)}
                >
                  Set password
                </Button>
                <Button variant="outline" size="sm" onClick={() => onPatch(u.id, { disabled: !u.disabled })}>
                  {u.disabled ? "Enable" : "Disable"}
                </Button>
              </div>
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
  );
}

function KeysTable({ keys, onRevoke }: { keys: AdminKey[]; onRevoke: (id: string) => void }) {
  return (
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
                <Button variant="outline" size="sm" onClick={() => onRevoke(k.id)}>
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
  );
}

function SitesTable({
  sites,
  onFreeze,
  onReporters,
}: {
  sites: AdminSite[];
  onFreeze: (s: AdminSite) => void;
  onReporters: (siteId: string) => void;
}) {
  return (
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
                <Button variant="outline" size="sm" onClick={() => onFreeze(s)}>
                  {s.frozen ? "Unfreeze" : "Freeze"}
                </Button>
                <Button variant="outline" size="sm" onClick={() => onReporters(s.site_id)}>
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
  );
}

function TrackedLicencesTable({
  tracked,
  neverActivated,
  planPick,
  changing,
  chasing,
  onPlanPick,
  onChangePlan,
  onChase,
}: {
  tracked: TrackedSite[];
  neverActivated: LicenceRow[];
  planPick: Record<string, string>;
  changing: string | null;
  chasing: string | null;
  onPlanPick: (siteId: string, edition: string) => void;
  onChangePlan: (siteId: string) => void;
  onChase: (siteId: string) => void;
}) {
  return (
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
          <TableHead>Plan</TableHead>
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
              <TableCell>
                <div className="flex items-center gap-2">
                  <select
                    className="flex h-8 rounded-md border border-input bg-transparent px-2 text-xs"
                    value={planPick[t.site_id] || t.edition || "pro"}
                    onChange={(e) => onPlanPick(t.site_id, e.target.value)}
                  >
                    {["simple", "pro", "premium", "trial"].map((ed) => (
                      <option key={ed} value={ed}>{ed}</option>
                    ))}
                  </select>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={changing === t.site_id}
                    onClick={() => onChangePlan(t.site_id)}
                  >
                    {changing === t.site_id ? "…" : "Set plan"}
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          );
        })}
        {neverActivated.map((l) => (
          <TableRow key={"na-" + l.site_id}>
            <TableCell className="font-medium">{l.site_id}</TableCell>
            <TableCell colSpan={6} className="text-muted-foreground">
              Key issued {l.issued_by ? `by ${l.issued_by}` : ""} — never activated (no push yet)
            </TableCell>
            <TableCell>
              <div className="flex items-center gap-2">
                <select
                  className="flex h-8 rounded-md border border-input bg-transparent px-2 text-xs"
                  value={planPick[l.site_id] || l.edition || "pro"}
                  onChange={(e) => onPlanPick(l.site_id, e.target.value)}
                >
                  {["simple", "pro", "premium", "trial"].map((ed) => (
                    <option key={ed} value={ed}>{ed}</option>
                  ))}
                </select>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={changing === l.site_id}
                  onClick={() => onChangePlan(l.site_id)}
                >
                  {changing === l.site_id ? "…" : "Set plan"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={chasing === l.site_id}
                  onClick={() => onChase(l.site_id)}
                >
                  {chasing === l.site_id ? "…" : "Re-send licence"}
                </Button>
              </div>
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
  );
}

function LicencesIssuedTable({ rows }: { rows: LicenceRow[] }) {
  return (
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
  );
}

function UsersTab({ onError }: { onError: (e: string | null) => void }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("client");
  const [siteId, setSiteId] = useState("");
  const [pwBusy, setPwBusy] = useState<string | null>(null);
  const [pwPanel, setPwPanel] = useState<{ user: AdminUser; value: string; done: boolean } | null>(null);

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
    // Fixture drives capture — no network needed while bones are captured.
    if (!isBuild) refresh();
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

  function passwordStart(u: AdminUser) {
    // Prefill a generated password — the admin can keep it or type their own.
    setPwPanel({ user: u, value: newPassword(), done: false });
  }

  async function passwordSave() {
    if (!pwPanel) return;
    setPwBusy(pwPanel.user.id);
    try {
      await api(`/api/admin/users/${pwPanel.user.id}/password`, {
        method: "POST",
        body: JSON.stringify({ password: pwPanel.value }),
      });
      setPwPanel({ ...pwPanel, done: true });
      onError(null);
    } catch (err) {
      onError(err instanceof Error ? err.message : "could not set password");
    } finally {
      setPwBusy(null);
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
        {pwPanel && (
          <div className="grid gap-2 rounded-lg border bg-muted p-4">
            {pwPanel.done ? (
              <>
                <p className="text-sm font-medium">
                  Password set for <code>{pwPanel.user.site_id ?? pwPanel.user.email}</code> — send it to the user:
                </p>
                <code className="break-all text-sm">{pwPanel.value}</code>
                <div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => navigator.clipboard?.writeText(pwPanel.value).catch(() => {})}
                  >
                    Copy password
                  </Button>
                </div>
              </>
            ) : (
              <>
                <p className="text-sm font-medium">
                  New password for <code>{pwPanel.user.email}</code> — keep the suggestion or type your own:
                </p>
                <Input
                  value={pwPanel.value}
                  onChange={(e) => setPwPanel({ ...pwPanel, value: e.target.value })}
                />
                <div className="flex gap-2">
                  <Button size="sm" disabled={pwBusy === pwPanel.user.id || pwPanel.value.length < 8} onClick={passwordSave}>
                    {pwBusy === pwPanel.user.id ? "Saving…" : "Save password"}
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setPwPanel(null)}>
                    Cancel
                  </Button>
                </div>
              </>
            )}
          </div>
        )}
        <BoneSkeleton
          name="admin-users"
          loading={loading}
          {...boneStyle}
          fixture={
            <UsersTable users={MOCK_USERS} pwBusy={null} pwPanel={null} onPatch={() => {}} onPasswordStart={() => {}} />
          }
          fallback={<Skeleton className="h-40" />}
        >
          <UsersTable users={users} pwBusy={pwBusy} pwPanel={pwPanel} onPatch={patch} onPasswordStart={passwordStart} />
        </BoneSkeleton>
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
    // Fixture drives capture — no network needed while bones are captured.
    if (!isBuild) refresh();
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
        <BoneSkeleton
          name="admin-keys"
          loading={loading}
          {...boneStyle}
          fixture={<KeysTable keys={MOCK_KEYS} onRevoke={() => {}} />}
          fallback={<Skeleton className="h-40" />}
        >
          <KeysTable keys={keys} onRevoke={revoke} />
        </BoneSkeleton>
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
    // Fixture drives capture — no network needed while bones are captured.
    if (!isBuild) refresh();
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
        <BoneSkeleton
          name="admin-sites"
          loading={loading}
          {...boneStyle}
          fixture={<SitesTable sites={MOCK_SITES} onFreeze={() => {}} onReporters={() => {}} />}
          fallback={<Skeleton className="h-40" />}
        >
          <SitesTable sites={sites} onFreeze={toggleFreeze} onReporters={loadReporters} />
        </BoneSkeleton>
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
  const [rows, setRows] = useState<LicenceRow[]>([]);
  const [tracked, setTracked] = useState<TrackedSite[]>([]);
  const [loading, setLoading] = useState(true);
  const [changing, setChanging] = useState<string | null>(null);
  const [chasing, setChasing] = useState<string | null>(null);
  const [planPick, setPlanPick] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [issue, setIssue] = useState({
    customer: "",
    siteId: "",
    contactEmail: "",
    packagerFp: "",
    edition: "pro",
    validityDays: "365",
  });
  const [issuing, setIssuing] = useState(false);
  const [issued, setIssued] = useState<{
    site_id: string;
    email: string;
    password?: string;
  } | null>(null);
  async function refresh() {
    try {
      const d = await api("/api/licenses");
      setRows(d.licences);
      const t = await api("/api/admin/site-licences");
      setTracked(t.tracked ?? []);
      onError(null);
    } catch (e) {
      onError(e instanceof Error ? e.message : "licences failed");
    }
  }
  useEffect(() => {
    // Fixture drives capture — no network needed while bones are captured.
    if (isBuild) return;
    (async () => {
      await refresh();
      setLoading(false);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  async function chaseActivation(siteId: string) {
    const lic = rows.find((l) => l.site_id === siteId);
    if (!lic) {
      setNotice(`No issued licence found for ${siteId}.`);
      return;
    }
    setChasing(siteId);
    setNotice(null);
    try {
      const res = await fetch("/api/licenses/renew", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ serial: lic.serial, edition: lic.edition }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.error ?? `re-issue failed (${res.status})`);
      const blob = new Blob([JSON.stringify(data.licence)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `license-${siteId}-${lic.edition}.lic`;
      a.click();
      URL.revokeObjectURL(a.href);
      setNotice(`${siteId}: fresh licence downloaded — send it to the site to activate.`);
      await refresh();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "chase failed");
    } finally {
      setChasing(null);
    }
  }
  async function changePlan(siteId: string) {
    const latestSerial = rows.filter((l) => l.site_id === siteId).map((l) => l.serial)[0];
    if (!latestSerial) {
      setNotice(`No issued licence found for ${siteId} — issue one from the Licence page first.`);
      return;
    }
    const edition = planPick[siteId] || "pro";
    setChanging(siteId);
    setNotice(null);
    try {
      const res = await fetch("/api/licenses/renew", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ serial: latestSerial, edition }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.error ?? `plan change failed (${res.status})`);
      const lic = JSON.stringify(data.licence);
      const blob = new Blob([lic], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `license-${siteId}-${edition}.lic`;
      a.click();
      URL.revokeObjectURL(a.href);
      setNotice(`${siteId} moved to ${edition} — new key downloaded, send it to the site.`);
      await refresh();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "plan change failed");
    } finally {
      setChanging(null);
    }
  }
  async function issueLicence(e: React.FormEvent) {
    e.preventDefault();
    setIssuing(true);
    setNotice(null);
    setIssued(null);
    try {
      const res = await fetch("/api/licenses/issue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          site_id: issue.siteId.trim(),
          customer: issue.customer.trim(),
          contact_email: issue.contactEmail.trim() || undefined,
          packager_fp: issue.packagerFp.trim(),
          edition: issue.edition,
          validity_days: Number(issue.validityDays) || 365,
        }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        const sug = Array.isArray(data?.suggestions) ? `: try ${data.suggestions.join(", ")}` : "";
        throw new Error(`${data?.error ?? `issuance failed (${res.status})`}${sug}`);
      }
      setIssued(data.client ?? { site_id: data.licence.site_id, email: "" });
      setNotice(
        data.client?.password
          ? `Licence issued for ${data.licence.site_id} — copy the customer password below and send it.`
          : `Licence issued for ${data.licence.site_id} (account already exists).`
      );
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "issuance failed");
    } finally {
      setIssuing(false);
    }
  }
  const trackedSites = new Set(tracked.map((t) => t.site_id));
  const neverActivated = rows
    .filter((l) => !trackedSites.has(l.site_id))
    .filter((l, i, a) => a.findIndex((x) => x.site_id === l.site_id) === i);
  const customers = new Set([...tracked.map((t) => t.site_id), ...rows.map((l) => l.site_id)]);
  const trials = tracked.filter((t) => t.edition === "trial");
  const endingSoon = tracked.filter((t) => t.days_left != null && t.days_left >= 0 && t.days_left <= 14);
  const overdue = tracked.filter((t) =>
    t.state === "blocked" || t.state === "grace" ||
    (t.edition === "trial" && t.days_left != null && t.days_left < 0));
  return (
    <div className="grid gap-6">
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
      {[
        { k: "Customers", v: String(customers.size) },
        { k: "Active trials", v: String(trials.filter((t) => t.days_left == null || t.days_left >= 0).length) },
        { k: "Ending ≤ 14d", v: String(endingSoon.length) },
        { k: "Overdue", v: String(overdue.length) },
        { k: "Never activated", v: String(neverActivated.length) },
      ].map((s) => (
        <Card key={s.k}>
          <CardContent className="pt-4">
            <div className="text-2xl font-bold">{s.v}</div>
            <div className="text-sm text-muted-foreground">{s.k}</div>
          </CardContent>
        </Card>
      ))}
    </div>
    {notice && <p className="text-sm font-medium">{notice}</p>}
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <HugeiconsIcon icon={File01Icon} size={18} /> Issue licence + onboard site
        </CardTitle>
        <CardDescription>
          First issue for a site creates its client account and a one-time setup code.
          Re-issues for an existing site return no code.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <form onSubmit={issueLicence} className="grid gap-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="ad-customer">Company name</Label>
              <Input
                id="ad-customer"
                placeholder="e.g. Ram Vendor"
                value={issue.customer}
                onChange={(e) => setIssue({ ...issue, customer: e.target.value })}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="ad-site">Site ID</Label>
              <div className="flex gap-2">
                <Input
                  id="ad-site"
                  placeholder="e.g. ram-vendor"
                  value={issue.siteId}
                  onChange={(e) => setIssue({ ...issue, siteId: e.target.value })}
                />
                <Button
                  type="button"
                  variant="outline"
                  disabled={!issue.customer.trim()}
                  onClick={() => setIssue({ ...issue, siteId: slugifySiteId(issue.customer) })}
                >
                  Slug it
                </Button>
              </div>
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="ad-contact">Contact email (required for new sites)</Label>
              <Input
                id="ad-contact"
                type="email"
                placeholder="owner@example.com"
                value={issue.contactEmail}
                onChange={(e) => setIssue({ ...issue, contactEmail: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">
                First licence for a site creates its login under this email. Re-issues need no email.
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="ad-fp">Machine ID (packager_fp, 32 hex chars)</Label>
              <Input
                id="ad-fp"
                value={issue.packagerFp}
                onChange={(e) => setIssue({ ...issue, packagerFp: e.target.value.trim() })}
              />
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label>Edition</Label>
              <select
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
                value={issue.edition}
                onChange={(e) => setIssue({ ...issue, edition: e.target.value })}
              >
                {["simple", "pro", "premium", "trial"].map((ed) => (
                  <option key={ed} value={ed}>{ed}</option>
                ))}
              </select>
            </div>
            <div className="grid gap-2">
              <Label>Valid for</Label>
              <select
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
                value={issue.validityDays}
                onChange={(e) => setIssue({ ...issue, validityDays: e.target.value })}
              >
                {[
                  { label: "6 months", days: 182 },
                  { label: "1 year", days: 365 },
                  { label: "2 years", days: 730 },
                  { label: "3 years", days: 1095 },
                ].map((p) => (
                  <option key={p.days} value={p.days}>{p.label}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <Button disabled={issuing || !issue.siteId.trim() || !issue.packagerFp.trim()}>
              {issuing ? "Issuing…" : "Issue licence"}
            </Button>
          </div>
        </form>
        {issued && (
          <div className="grid gap-2 rounded-lg border bg-muted p-4">
            <p className="text-sm font-medium">Site ID: <code>{issued.site_id}</code></p>
            {issued.email && <p className="text-sm text-muted-foreground">Client login: <code>{issued.email}</code></p>}
            {issued.password ? (
              <>
                <p className="text-sm font-medium">Customer password — copy now:</p>
                <code className="break-all text-sm">{issued.password}</code>
                <p className="text-xs text-muted-foreground">
                  It will not be shown again. Send it with the site ID — the customer
                  logs in with Site ID + password.
                </p>
                <div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => navigator.clipboard?.writeText(issued.password ?? "").catch(() => {})}
                  >
                    Copy password
                  </Button>
                </div>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">No new account — this site already has a client login.</p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
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
        <BoneSkeleton
          name="admin-licences-tracking"
          loading={loading}
          {...boneStyle}
          fixture={
            <TrackedLicencesTable
              tracked={MOCK_TRACKED}
              neverActivated={MOCK_LICENCES.filter((l) => !MOCK_TRACKED.some((t) => t.site_id === l.site_id))}
              planPick={{}}
              changing={null}
              chasing={null}
              onPlanPick={() => {}}
              onChangePlan={() => {}}
              onChase={() => {}}
            />
          }
          fallback={<Skeleton className="h-40" />}
        >
          <TrackedLicencesTable
            tracked={tracked}
            neverActivated={neverActivated}
            planPick={planPick}
            changing={changing}
            chasing={chasing}
            onPlanPick={(siteId, edition) => setPlanPick({ ...planPick, [siteId]: edition })}
            onChangePlan={changePlan}
            onChase={chaseActivation}
          />
        </BoneSkeleton>
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
        <BoneSkeleton
          name="admin-licences-issued"
          loading={loading}
          {...boneStyle}
          fixture={<LicencesIssuedTable rows={MOCK_LICENCES} />}
          fallback={<Skeleton className="h-40" />}
        >
          <LicencesIssuedTable rows={rows} />
        </BoneSkeleton>
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
