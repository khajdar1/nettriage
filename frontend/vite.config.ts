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
    // Tests stand in for the API by replacing fetch (src/test/fakeApi.ts); put it back after each.
    unstubGlobals: true,
    restoreMocks: true,
    // The palette test reads styles.css as text (src/styles.test.ts); Vitest blanks CSS otherwise.
    css: { include: [/styles\.css/] },
    // Vitest's default include glob also matches scripts/check-csp.test.mjs,
    // a node:test file meant to run only under `node --test`. Scope Vitest to src/.
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
