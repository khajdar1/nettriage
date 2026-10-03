import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  // src/api/schema.ts is generated from openapi.json by `just openapi`.
  { ignores: ["dist", "node_modules", "src/api/schema.ts"] },
  js.configs.recommended,
  ...tseslint.configs.strict,
  { files: ["src/**/*.{ts,tsx}"], languageOptions: { globals: globals.browser } },
  { files: ["scripts/**/*.mjs", "*.config.{js,ts}"], languageOptions: { globals: globals.node } },
]);
