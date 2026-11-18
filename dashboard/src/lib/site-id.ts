// Site IDs: the login identifier for CLIENT users (vendor admins use email).
// slugifySiteId("Ram Vendor") -> "ram-vendor".
//
// Uniqueness is enforced by the database unique index
// (migrations/0005: UNIQUE INDEX ON users(site_id) WHERE role = 'client').
// The bloom filter below is a FAST PRE-CHECK ONLY: a "maybe taken" answer
// MUST still be confirmed against the database, and the database constraint
// is the authority (the in-memory filter is best-effort: cold starts begin
// empty and Worker isolates do not share memory). Never reject a create
// request on bloom alone.

export function slugifySiteId(name: string): string {
  const ascii = name.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
  const slug = ascii
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug;
}

/** Next free IDs for a taken base: `ram-vendor` -> `ram-vendor-2`, `-3`, ... */
export async function suggestSiteIds(
  base: string,
  count: number,
  isTaken: (id: string) => Promise<boolean> | boolean
): Promise<string[]> {
  const out: string[] = [];
  let n = 2;
  if (!(await isTaken(base))) return [base];
  while (out.length < count) {
    const cand = `${base}-${n++}`;
    if (!(await isTaken(cand))) out.push(cand);
  }
  return out;
}

// ---------- tiny bloom filter (pre-check only, see header comment) ----------

// Sized for a few thousand sites: 8192 bits (~1 KiB) with 3 hash functions.
// Expected false-positive rate at 5000 entries is high (~30%) — that is
// fine, because positives are always re-checked against the database; the
// filter only lets definite-negatives skip the query fast path.
const BLOOM_BITS = 8192;

function fnv1a(str: string, seed: number): number {
  let h = seed >>> 0;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

export class SiteIdBloom {
  private bits = new Uint8Array(BLOOM_BITS / 8);

  private positions(id: string): [number, number, number] {
    const s = id.toLowerCase();
    return [
      fnv1a(s, 0x811c9dc5) % BLOOM_BITS,
      fnv1a(s, 0x01000193) % BLOOM_BITS,
      fnv1a(s, 0xdeadbeef) % BLOOM_BITS,
    ];
  }

  add(id: string): void {
    for (const p of this.positions(id)) this.bits[p >> 3] |= 1 << (p & 7);
  }

  /** True = MAYBE taken (confirm against the DB). False = definitely unseen. */
  maybeTaken(id: string): boolean {
    return this.positions(id).every((p) => (this.bits[p >> 3] & (1 << (p & 7))) !== 0);
  }
}

// Module-level pre-check instance. Seeding is tracked per backing store
// (WeakSet below): the first availability check for a store seeds the filter
// from its user table, and every client insert notes its site_id — so a
// bloom negative means "not in this store" and can skip the query, while a
// positive is always re-checked against the database (the unique index is
// the authority; cross-store entries only ever cause an extra confirming
// query, never a wrong answer).
let bloom = new SiteIdBloom();
const seededStores = new WeakSet<object>();

export function _resetBloomForTests(): void {
  bloom = new SiteIdBloom();
}

export async function ensureBloomSeeded(
  storeKey: object,
  loadIds: () => Promise<string[]>
): Promise<void> {
  if (seededStores.has(storeKey)) return;
  for (const id of await loadIds()) if (id) bloom.add(id);
  seededStores.add(storeKey);
}

export function noteSiteId(id: string): void {
  bloom.add(id);
}

export function bloomMaybeTaken(id: string): boolean {
  return bloom.maybeTaken(id);
}

// ---------- shared credential helper ----------

// Easy-to-read base32 without 0/O/1/I (no ambiguous glyphs for phone read-out).
const CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

/** Generated password for a new/reset customer account. The vendor sees it
 * once and shares it out-of-band (WhatsApp/phone). 12 chars from a 32-glyph
 * unambiguous alphabet — typeable on a phone, well past the 8-char minimum. */
export function newPassword(length = 12): string {
  const rand = crypto.getRandomValues(new Uint8Array(length));
  return [...rand].map((b) => CODE_ALPHABET[b % CODE_ALPHABET.length]).join("");
}
