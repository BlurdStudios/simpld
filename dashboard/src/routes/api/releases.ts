// Releases (R2-backed installer channel for the client Download page).
// Manifest reuses the UPDATES.md latest.json shape:
//   { version, file, sha256, notes }
// R2 layout:  releases/<file>  installer bytes
//             releases/latest.json  current manifest (single object)
//             releases/index.json   all published versions, newest first
//
//   GET /api/releases            list (session auth; client: any site)
//   GET /api/releases/file/:name installer bytes (session auth)
//   POST /api/admin/releases     multipart upload (vendor_admin only):
//        fields: file (Simpld-Setup-X.Y.Z.exe), notes?, force? ("1" allows downgrade)

import { validateReleaseManifest, type ReleaseManifest } from "../../lib/contract";
import { json } from "../../lib/portal";

export interface R2Object {
  body: Uint8Array;
  size: number;
}

export interface R2Like {
  get(key: string): Promise<R2Object | null>;
  put(key: string, data: Uint8Array | ArrayBuffer): Promise<void>;
}

const LATEST_KEY = "releases/latest.json";
const INDEX_KEY = "releases/index.json";
const INSTALLER_RE = /^Simpld-Setup-(\d+(?:\.\d+)*)\.exe$/;

function cmpVersions(a: string, b: string): number {
  const pa = a.split(".").map(Number);
  const pb = b.split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d !== 0) return d;
  }
  return 0;
}

export async function sha256HexBytes(data: Uint8Array | ArrayBuffer): Promise<string> {
  const buf: ArrayBuffer = data instanceof Uint8Array ? data.slice().buffer : data.slice(0);
  const d = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function readJson<T>(r2: R2Like, key: string): Promise<T | null> {
  const o = await r2.get(key);
  if (!o) return null;
  try {
    return JSON.parse(new TextDecoder().decode(o.body)) as T;
  } catch {
    return null;
  }
}

export async function handleListReleases(r2: R2Like | null): Promise<Response> {
  if (!r2) return json({ error: "releases not configured (R2 RELEASES binding)" }, 501);
  const index = (await readJson<ReleaseManifest[]>(r2, INDEX_KEY)) ?? [];
  const latest = await readJson<ReleaseManifest>(r2, LATEST_KEY);
  return json({ ok: true, latest, releases: index.filter(validateReleaseManifest) });
}

export async function handleDownloadRelease(r2: R2Like | null, name: string): Promise<Response> {
  if (!r2) return json({ error: "releases not configured (R2 RELEASES binding)" }, 501);
  if (!INSTALLER_RE.test(name)) return json({ error: "unknown file" }, 404);
  const obj = await r2.get(`releases/${name}`);
  if (!obj) return json({ error: "not published" }, 404);
  return new Response(obj.body as unknown as BodyInit, {
    status: 200,
    headers: {
      "Content-Type": "application/octet-stream",
      "Content-Length": String(obj.size),
      "Content-Disposition": `attachment; filename="${name}"`,
    },
  });
}

export async function handleUploadRelease(req: Request, r2: R2Like | null): Promise<Response> {
  if (!r2) return json({ error: "releases not configured (R2 RELEASES binding)" }, 501);
  let form: FormData;
  try {
    form = await req.formData();
  } catch {
    return json({ error: "multipart form with a 'file' field required" }, 400);
  }
  const file = form.get("file");
  if (!(file instanceof File)) return json({ error: "'file' (installer .exe) required" }, 400);
  const m = INSTALLER_RE.exec(file.name);
  if (!m) return json({ error: "file must match Simpld-Setup-X.Y.Z.exe" }, 400);
  const version = m[1];
  const notes = typeof form.get("notes") === "string" ? (form.get("notes") as string).slice(0, 500) : "";
  const force = form.get("force") === "1" || form.get("force") === "true";
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes.length === 0) return json({ error: "empty file" }, 400);
  const current = await readJson<ReleaseManifest>(r2, LATEST_KEY);
  if (current && !force && cmpVersions(version, current.version) <= 0) {
    return json({ error: `downgrade refused: ${version} <= current ${current.version} (resend with force=1)` }, 409);
  }
  const sha256 = await sha256HexBytes(bytes);
  const manifest: ReleaseManifest = { version, file: file.name, sha256, notes };
  if (!validateReleaseManifest(manifest)) return json({ error: "manifest validation failed" }, 500);
  await r2.put(`releases/${file.name}`, bytes);
  await r2.put(LATEST_KEY, new TextEncoder().encode(JSON.stringify(manifest)));
  const index = ((await readJson<ReleaseManifest[]>(r2, INDEX_KEY)) ?? []).filter((e) => e.file !== file.name);
  index.unshift(manifest);
  await r2.put(INDEX_KEY, new TextEncoder().encode(JSON.stringify(index)));
  return json({ ok: true, release: manifest }, 201);
}
