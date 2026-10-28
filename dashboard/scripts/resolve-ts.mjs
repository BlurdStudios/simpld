// ESM resolve hook: lets node type-stripping resolve extensionless relative
// imports (-> .ts/.tsx) and the `@/` Vite alias (-> dashboard/src).
// Used ONLY by scripts/smoke.ts so the smoke suite exercises the real modules.

import { existsSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const SCRIPTS_DIR = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(SCRIPTS_DIR, "..", "src");

function tryCandidates(base) {
  if (base.endsWith(".ts") || base.endsWith(".tsx") || base.endsWith(".js")) {
    return existsSync(base) ? base : null;
  }
  for (const c of [`${base}.ts`, `${base}.tsx`, path.join(base, "index.ts")]) {
    if (existsSync(c)) return c;
  }
  return null;
}

export async function resolve(specifier, context, next) {
  if (specifier === "@" || specifier.startsWith("@/")) {
    const rel = specifier === "@" ? "" : specifier.slice(2);
    const found = tryCandidates(path.join(SRC, rel));
    if (found) return { url: pathToFileURL(found).href, shortCircuit: true };
  }
  if (
    (specifier.startsWith("./") || specifier.startsWith("../") || specifier.startsWith("/")) &&
    context.parentURL &&
    context.parentURL.startsWith("file:")
  ) {
    const base = specifier.startsWith("/")
      ? specifier
      : path.resolve(path.dirname(fileURLToPath(context.parentURL)), specifier);
    const found = tryCandidates(base);
    if (found) return { url: pathToFileURL(found).href, shortCircuit: true };
  }
  return next(specifier, context);
}
