import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
  build: {
    // Never inline assets as data: URIs; the CSP only allows data: for images.
    assetsInlineLimit: 0,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    // Vitest's default include glob also matches scripts/check-csp.test.mjs,
    // a node:test file meant to run only under `node --test`. Scope Vitest to src/.
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
