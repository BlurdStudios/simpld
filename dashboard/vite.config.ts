import { TanStackRouterVite } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [
    TanStackRouterVite({
      autoCodeSplitting: true,
      // API handlers live under src/routes/api/ by contract but are NOT pages —
      // they are imported by the Worker entry (src/worker.ts). The api/
      // directory entry is excluded wholesale; push-* files predate that.
      routeFileIgnorePattern: "push-|^api$",
    }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: { port: 5173 },
});
