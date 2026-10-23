import { defaultSchema } from "rehype-sanitize";

/**
 * Sanitization schema for chat markdown that is rendered with `rehype-raw`.
 *
 * `rehype-raw` turns embedded HTML in a message into real DOM nodes, which is
 * required so things like `<video>`/`<img>` actually render instead of showing
 * as literal text. On its own that is an XSS vector — `<script>`, inline event
 * handlers (`onerror=…`), and `javascript:` URLs would all execute.
 *
 * This schema is the safety layer: it starts from rehype-sanitize's strict
 * default (which drops `<script>`, all `on*` handlers, and unsafe URL schemes)
 * and then explicitly re-allows the media elements + attributes we want to
 * support. Anything not listed here is stripped.
 *
 * Use it AFTER rehypeRaw:
 *   rehypePlugins={[rehypeRaw, [rehypeSanitize, markdownSanitizeSchema]]}
 */
export const markdownSanitizeSchema = {
  ...defaultSchema,
  tagNames: [
    ...(defaultSchema.tagNames ?? []),
    "video",
    "audio",
    "source",
    "iframe",
  ],
  attributes: {
    ...defaultSchema.attributes,
    video: [
      "controls",
      "width",
      "height",
      "poster",
      "preload",
      "loop",
      "muted",
      "playsinline",
      "src",
    ],
    audio: ["controls", "preload", "loop", "muted", "src"],
    source: ["src", "srcset", "type", "media", "sizes"],
    // iframe is the riskiest tag. We allow only safe layout/embed attributes;
    // `sandbox` is enforced by the component override, and `src` is restricted
    // to http(s) by the `protocols` config below — so no javascript: URLs.
    iframe: [
      "src",
      "width",
      "height",
      "title",
      "allow",
      "allowfullscreen",
      "loading",
      "referrerpolicy",
    ],
    img: [
      ...((defaultSchema.attributes?.img as unknown[]) ?? []),
      "loading",
      "width",
      "height",
    ],
  },
  protocols: {
    ...defaultSchema.protocols,
    // Only permit http(s) (and protocol-relative) sources for media elements.
    src: ["http", "https"],
  },
} as typeof defaultSchema;
