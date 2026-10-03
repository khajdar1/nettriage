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
  {
    // The CSP allows no inline styles, and the app never renders HTML it was given (spec §10).
    files: ["src/**/*.tsx"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='style']",
          message: "The CSP allows no inline styles: add a class to styles.css.",
        },
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "Render text, never HTML (spec §10).",
        },
      ],
    },
  },
  { files: ["scripts/**/*.mjs", "*.config.{js,ts}"], languageOptions: { globals: globals.node } },
]);
