// Password + token primitives (WebCrypto only — runs in Workers and Node 19+).
// Passwords: PBKDF2-SHA256, 100k iterations, 16B salt, 32B derived key.
// Stored form: "pbkdf2-sha256$<iter>$<salt b64>$<hash b64>".

const ITERATIONS = 100_000;

function b64encode(raw: Uint8Array): string {
  let s = "";
  for (let i = 0; i < raw.length; i++) s += String.fromCharCode(raw[i]);
  return btoa(s);
}

function b64decode(b64: string): Uint8Array {
  const s = atob(b64.trim());
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

function timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i];
  return d === 0;
}

async function pbkdf2(password: string, salt: Uint8Array, iter: number): Promise<Uint8Array> {
  const enc = new TextEncoder().encode(password);
  const pw = new Uint8Array(enc.length);
  pw.set(enc);
  const saltCopy = new Uint8Array(salt.length);
  saltCopy.set(salt);
  const key = await crypto.subtle.importKey(
    "raw",
    pw.buffer,
    { name: "PBKDF2" },
    false,
    ["deriveBits"]
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt: saltCopy.buffer, iterations: iter },
    key,
    256
  );
  return new Uint8Array(bits);
}

export async function hashPassword(password: string): Promise<string> {
  if (password.length < 8) throw new Error("password must be at least 8 characters");
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const hash = await pbkdf2(password, salt, ITERATIONS);
  return `pbkdf2-sha256$${ITERATIONS}$${b64encode(salt)}$${b64encode(hash)}`;
}

export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  try {
    const parts = stored.split("$");
    if (parts.length !== 4 || parts[0] !== "pbkdf2-sha256") return false;
    const iter = Number(parts[1]);
    if (!Number.isInteger(iter) || iter < 10_000 || iter > 2_000_000) return false;
    const salt = b64decode(parts[2]);
    const want = b64decode(parts[3]);
    const got = await pbkdf2(password, salt, iter);
    return timingSafeEqual(got, want);
  } catch {
    return false;
  }
}

/** Random hex token (sessions). */
export function newToken(bytes = 32): string {
  const raw = crypto.getRandomValues(new Uint8Array(bytes));
  return [...raw].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** New push/viewer API key (plaintext, shown once; only sha256 is stored). */
export function newApiKey(): string {
  const raw = crypto.getRandomValues(new Uint8Array(24));
  const b64 = b64encode(raw).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
  return `sk_${b64}`;
}

/** SHA-256 hex of a key — what is stored and compared. */
export async function sha256Hex(s: string): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function newId(prefix: string): string {
  return `${prefix}_${newToken(8)}`;
}

export function nowIso(): string {
  return new Date().toISOString();
}
