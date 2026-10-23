"use client";

import type { ReactNode } from "react";

import { useRecoveredImage } from "@/lib/remoteImage";
import { cn } from "@/platform/utils";

/**
 * A stored icon or avatar, painted whichever way the asset allows.
 *
 * The service's default icons sit in S3 as `binary/octet-stream`, which a plain
 * `<img>` cannot decode — and the SVG ones Chrome refuses outright — so the
 * swatches rendered as empty boxes. `useRecoveredImage` retries a refused asset
 * over CORS and hands back either a blob URL or sanitised SVG markup to inline;
 * inlining is what lets an icon that strokes with `currentColor` take the colour
 * of the swatch it sits in.
 *
 * `fallback` is drawn when neither route works, so a choice is always visible
 * even if its asset is not.
 */
export default function RemoteAsset({
  url,
  className,
  fallback,
}: {
  url?: string;
  className: string;
  fallback: ReactNode;
}) {
  const { src, markup, failed, onError } = useRecoveredImage(url);

  if (failed || (!src && !markup)) return <>{fallback}</>;

  if (markup)
    return (
      <span
        aria-hidden
        className={cn(
          "inline-flex items-center justify-center [&>svg]:size-full",
          className,
        )}
        dangerouslySetInnerHTML={{ __html: markup }}
      />
    );

  return <img src={src} alt="" className={className} onError={onError} />;
}
