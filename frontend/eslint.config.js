import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  { ignores: ["dist", "node_modules"] },
  js.configs.recommended,
  ...tseslint.configs.strict,
  { files: ["src/**/*.{ts,tsx}"], languageOptions: { globals: globals.browser } },
  { files: ["scripts/**/*.mjs", "*.config.{js,ts}"], languageOptions: { globals: globals.node } },
]);
