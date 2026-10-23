/** @type {import("prettier").Config} */
const config = {
  semi: true,
  singleQuote: false,
  tabWidth: 2,
  trailingComma: "all",
  printWidth: 80,
  plugins: ["@ianvs/prettier-plugin-sort-imports"],

  // Import sorting configuration
  importOrder: [
    // React and Next.js (always first)
    "^react$",
    "^react-dom$",
    "^next$",
    "^next/(.*)$",

    // Empty string creates a blank line separator
    "",

    // External packages (node_modules)
    "<THIRD_PARTY_MODULES>",

    "",

    // Internal aliases - adjust based on your tsconfig paths
    "^@api(.*)$",
    "^@configs(.*)$",
    "^@hooks(.*)$",
    "^@components(.*)$",
    "^@/(.*)$",

    "",

    // Relative imports (parent directories first, then current/child)
    "^\\.\\./",
    "^\\./",
  ],
  // Type imports will be sorted with their value imports
  importOrderTypeScriptVersion: "5.0.0",
};

export default config;
