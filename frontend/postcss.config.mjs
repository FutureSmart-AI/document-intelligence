// Same PostCSS setup as the root agent UI (`../../postcss.config.mjs`): this app
// is styled with Tailwind v4 and the shared UI kit imported from `@agent/*`, so
// both workspaces render from one design system.
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
