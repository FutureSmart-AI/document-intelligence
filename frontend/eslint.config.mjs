import { dirname } from "path";
import { fileURLToPath } from "url";

import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

const __dirname = dirname(fileURLToPath(import.meta.url));

export default tseslint.config(
  {
    ignores: [
      "**/.next/**",
      "**/out/**",
      "**/node_modules/**",
      "playwright-report/**",
      "test-results/**",
      "next-env.d.ts",
    ],
  },
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{js,jsx,ts,tsx}"],
    languageOptions: {
      parserOptions: {
        tsconfigRootDir: __dirname,
      },
    },
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "no-console": ["warn", { allow: ["warn", "error"] }],
    },
  },
  {
    files: ["**/__tests__/**/*.{js,jsx,ts,tsx}", "**/*.config.{js,cjs,mjs}"],
    rules: {
      "@typescript-eslint/no-require-imports": "off",
    },
  },
  // Build scripts and the dev preview print their progress on purpose.
  {
    files: ["scripts/**/*.ts"],
    rules: { "no-console": "off" },
  },
  {
    files: ["**/*.tsx"],
    // A fixture at the top of a spec is the input its assertions are written
    // against, not a value someone will want to tune, so tests are exempt.
    ignores: ["**/constants.tsx", "**/__tests__/**", "**/*.test.tsx"],
    rules: {
      "no-restricted-syntax": [
        "warn",
        {
          selector:
            "VariableDeclaration[kind='const'] > VariableDeclarator[id.name=/^[A-Z][A-Z0-9_]{2,}$/]",
          message:
            "SCREAMING_CASE constants should be moved to a dedicated constants.ts file.",
        },
      ],
    },
  },
);
