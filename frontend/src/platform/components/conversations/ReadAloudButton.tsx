import { memo } from "react";

import { Loader2, Pause, Play } from "lucide-react";

import type { TtsStatus } from "@/platform/chat-core/tts";

import Tooltip from "../ui/Tooltip";

interface ReadAloudButtonProps {
  status: TtsStatus;
  disabled?: boolean;
  onPlay: () => void;
  onPause: () => void;
  onResume: () => void;
  className?: string;
}

/**
 * Flat icon button for the message actions row, styled to match
 * `copy-to-clipboard.tsx`. Presentational — playback state and handlers come
 * from the `useTextToSpeech` hook owned by ChatMessage. Clicking while playing
 * pauses; clicking while paused resumes from the same position.
 */
export const ReadAloudButton = memo(
  ({
    status,
    disabled,
    onPlay,
    onPause,
    onResume,
    className,
  }: ReadAloudButtonProps) => {
    const isLoading = status === "loading";
    const isPlaying = status === "playing";
    const isPaused = status === "paused";
    const label = isLoading
      ? "Generating audio…"
      : isPlaying
        ? "Pause"
        : isPaused
          ? "Resume"
          : "Read aloud";
    const Icon = isLoading ? Loader2 : isPlaying ? Pause : Play;

    return (
      <Tooltip content={label} side="bottom">
        <button
          type="button"
          aria-label={label}
          disabled={disabled || isLoading}
          onClick={() =>
            isPlaying ? onPause() : isPaused ? onResume() : onPlay()
          }
          className={`inline-flex cursor-pointer items-center gap-1 bg-transparent text-slate-700 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50 ${className ?? ""}`}
        >
          <Icon className={`size-4 ${isLoading ? "animate-spin" : ""}`} />
        </button>
      </Tooltip>
    );
  },
);

ReadAloudButton.displayName = "ReadAloudButton";
