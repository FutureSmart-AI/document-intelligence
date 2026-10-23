import type { ReactNode } from "react";

import type { MediaMatch } from "./media";

/**
 * Trusted media embed components.
 *
 * These hold the ONLY embed markup in the app. Because they are rendered from
 * UI-detected, validated references (never from model HTML), the markup is
 * hardcoded-correct: youtube-nocookie, a referrer policy that lets YouTube
 * authorize playback (the Error 153 fix), a restrictive sandbox, and an
 * http(s)-only source. Each is capped to the message width so nothing overflows.
 */

export function YouTubeEmbed({ id, title }: { id: string; title?: string }) {
  return (
    <div className="my-3 max-w-full">
      <iframe
        className="w-full aspect-video rounded-lg border-0"
        src={`https://www.youtube-nocookie.com/embed/${id}`}
        title={title ?? "YouTube video player"}
        loading="lazy"
        // strict-origin-when-cross-origin sends the origin (not the full URL) so
        // YouTube authorizes the embed without leaking the page path.
        referrerPolicy="strict-origin-when-cross-origin"
        sandbox="allow-scripts allow-same-origin allow-popups allow-presentation"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share; fullscreen"
        allowFullScreen
      />
    </div>
  );
}

export function VideoEmbed({ src }: { src: string }) {
  return (
    <video
      controls
      preload="metadata"
      className="my-3 max-w-full h-auto rounded-lg block"
      style={{ maxWidth: "100%" }}
      src={src}
    />
  );
}

export function AudioEmbed({ src }: { src: string }) {
  return (
    <audio
      controls
      preload="metadata"
      className="my-3 w-full max-w-full block"
      style={{ maxWidth: "100%" }}
      src={src}
    />
  );
}

/** Render the trusted embed for a detected media reference. */
export function MediaEmbed({ match }: { match: MediaMatch }): ReactNode {
  switch (match.type) {
    case "youtube":
      return <YouTubeEmbed id={match.id} />;
    case "video":
      return <VideoEmbed src={match.url} />;
    case "audio":
      return <AudioEmbed src={match.url} />;
    default:
      return null;
  }
}
