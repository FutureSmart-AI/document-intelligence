"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Magic-byte prefixes for the raster formats the widget accepts, so a stored
 * asset can be re-labelled from its bytes rather than its (wrong) header.
 */
const SIGNATURES: [number[], string][] = [
  [[0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], "image/png"],
  [[0xff, 0xd8, 0xff], "image/jpeg"],
  [[0x47, 0x49, 0x46, 0x38], "image/gif"],
];

const ascii = (bytes: Uint8Array, from: number, to: number) =>
  String.fromCharCode(...bytes.subarray(from, to));

/** The image media type these bytes really are, or undefined if they aren't one. */
function sniffImageType(bytes: Uint8Array): string | undefined {
  for (const [signature, type] of SIGNATURES)
    if (signature.every((byte, index) => bytes[index] === byte)) return type;
  if (ascii(bytes, 0, 4) === "RIFF" && ascii(bytes, 8, 12) === "WEBP")
    return "image/webp";
  const head = new TextDecoder().decode(bytes.subarray(0, 1024)).trimStart();
  if (/^(<\?xml|<!--|<!DOCTYPE svg|<svg)/i.test(head)) return "image/svg+xml";
  return undefined;
}

/**
 * The SVG vocabulary an icon needs. Anything outside it — `script`, `style`,
 * `foreignObject`, `handler` — is dropped, because inlined markup runs in this
 * page's origin and the bytes come from an upload.
 */
const SVG_ELEMENTS = new Set([
  "svg",
  "g",
  "defs",
  "symbol",
  "use",
  "title",
  "desc",
  "metadata",
  "path",
  "rect",
  "circle",
  "ellipse",
  "line",
  "polyline",
  "polygon",
  "text",
  "tspan",
  "textPath",
  "clipPath",
  "mask",
  "pattern",
  "marker",
  "linearGradient",
  "radialGradient",
  "stop",
  "switch",
]);

/** Strip an attribute that could fetch or execute something. */
function scrubAttributes(element: Element) {
  for (const { name, value } of Array.from(element.attributes)) {
    const lowerName = name.toLowerCase();
    const lowerValue = value.replace(/\s+/g, "").toLowerCase();
    const isReference = lowerName === "href" || lowerName.endsWith(":href");
    if (
      lowerName.startsWith("on") ||
      lowerValue.includes("javascript:") ||
      // A same-document `#id` reference is the only one an icon needs; anything
      // else reaches off this page.
      (isReference && !lowerValue.startsWith("#")) ||
      (lowerName === "style" && lowerValue.includes("url("))
    )
      element.removeAttribute(name);
  }
}

/**
 * The asset's markup, reduced to drawing instructions, or undefined if it does
 * not parse as SVG.
 *
 * The root's `width`/`height` are dropped so the caller's classes size the
 * glyph, with a `viewBox` synthesised from them first when the file has none —
 * without one, a sized-by-CSS SVG collapses.
 */
export function sanitizeSvg(markup: string): string | undefined {
  const parsed = new DOMParser().parseFromString(markup, "image/svg+xml");
  const root = parsed.documentElement;
  if (
    !root ||
    root.localName.toLowerCase() !== "svg" ||
    parsed.getElementsByTagName("parsererror").length
  )
    return undefined;

  const walk = (element: Element) => {
    scrubAttributes(element);
    for (const child of Array.from(element.children)) {
      if (!SVG_ELEMENTS.has(child.localName)) child.remove();
      else walk(child);
    }
  };
  walk(root);

  if (!root.getAttribute("viewBox")) {
    const width = Number.parseFloat(root.getAttribute("width") ?? "");
    const height = Number.parseFloat(root.getAttribute("height") ?? "");
    if (width > 0 && height > 0)
      root.setAttribute("viewBox", `0 0 ${width} ${height}`);
  }
  root.removeAttribute("width");
  root.removeAttribute("height");

  return new XMLSerializer().serializeToString(root);
}

type Phase = "direct" | "recovering" | "failed";

/**
 * An image source that survives a mislabelled asset.
 *
 * Shared, verbatim, with the NL2SQL product's copy of this file: it is the same
 * bucket serving the same widget assets to both, and the sniffing rules are the
 * asset store's, not either product's.
 *
 * The widget's default icons and avatars live in S3 as `binary/octet-stream`.
 * A browser picks its image decoder from the media type — an SVG has no magic
 * bytes to fall back on — so `<img>` cannot paint them, and the ones whose
 * bytes start with `<?xml` are refused outright by Chrome's opaque response
 * blocking. The bucket does send `Access-Control-Allow-Origin: *`, and neither
 * problem applies to a CORS fetch, so a refused asset is fetched and sniffed:
 * an SVG comes back as sanitised `markup` to inline, anything else as a blob
 * URL carrying the right media type.
 *
 * Inlining rather than re-blobbing the SVG is deliberate: these icons stroke
 * with `currentColor`, which resolves to plain black inside an `<img>` and can
 * only follow the brand colour as part of this document. Inlined markup is
 * live DOM, hence `sanitizeSvg`.
 *
 * Wiring `onError` keeps the happy path a plain `<img src>` — the extra request
 * only happens for an asset the browser already rejected. `failed` means no
 * route worked and the caller should draw its own fallback.
 */
export function useRecoveredImage(url?: string) {
  const [phase, setPhase] = useState<Phase>("direct");
  const [recovered, setRecovered] = useState<{ src?: string; markup?: string }>(
    {},
  );

  // A new URL deserves a fresh direct attempt.
  useEffect(() => {
    setPhase("direct");
    setRecovered({});
  }, [url]);

  useEffect(() => {
    if (phase !== "recovering" || !url) return () => undefined;
    const controller = new AbortController();
    let objectUrl: string | undefined;
    void (async () => {
      try {
        const response = await fetch(url, {
          mode: "cors",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const bytes = new Uint8Array(await response.arrayBuffer());
        const type = sniffImageType(bytes);
        if (!type) throw new Error("not an image");
        if (type === "image/svg+xml") {
          const markup = sanitizeSvg(new TextDecoder().decode(bytes));
          if (!markup) throw new Error("unparsable SVG");
          setRecovered({ markup });
          return;
        }
        objectUrl = URL.createObjectURL(new Blob([bytes], { type }));
        setRecovered({ src: objectUrl });
      } catch {
        if (!controller.signal.aborted) setPhase("failed");
      }
    })();
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [phase, url]);

  const onError = useCallback(() => {
    setPhase((current) => (current === "direct" ? "recovering" : "failed"));
  }, []);

  return {
    src: recovered.src ?? (recovered.markup ? undefined : url),
    markup: recovered.markup,
    failed: phase === "failed",
    onError,
  };
}
