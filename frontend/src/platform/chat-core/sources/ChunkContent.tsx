"use client";

import { useState } from "react";

import { Check, Copy, ExternalLink, Quote } from "lucide-react";

import { copyToClipboard } from "../utils/clipboard";
import { cn } from "../utils/cn";
import { useRenderMarkdown, useSourcesTheme } from "./context";
import { IconBtn } from "./primitives";

interface ChunkContentProps {
  text?: string;
}

const ChunkContent = ({ text }: ChunkContentProps) => {
  const theme = useSourcesTheme();
  const renderMarkdown = useRenderMarkdown();
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await copyToClipboard(text || "");
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  const handleOpen = () => {
    if (!text) return;
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener");
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  if (!text) {
    return (
      <div
        className={cn(
          "rounded-md border p-3 text-xs",
          theme.surfaceMuted,
          theme.textMuted,
        )}
      >
        No content available
      </div>
    );
  }

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <span
          className={cn(
            "flex items-center gap-1.5 text-[11.5px] font-semibold",
            theme.textMuted,
          )}
        >
          <Quote className="size-3" />
          Chunk content
        </span>
        <div className="flex gap-1">
          <IconBtn
            icon={copied ? Check : Copy}
            onClick={handleCopy}
            label="Copy chunk content"
            active={copied}
          />
          <IconBtn
            icon={ExternalLink}
            onClick={handleOpen}
            label="Open chunk content in new tab"
          />
        </div>
      </div>
      <div
        data-testid="chunk-content-scroll"
        className={cn(
          "max-h-[280px] max-w-none overflow-auto rounded-md border px-3.5 py-3 text-[12px] leading-relaxed",
          theme.chunkPre,
          theme.prose,
          theme.textBody,
        )}
      >
        {renderMarkdown(text)}
      </div>
    </div>
  );
};

export default ChunkContent;
