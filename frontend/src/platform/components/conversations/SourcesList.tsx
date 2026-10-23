"use client";

import { useState } from "react";

import { BookOpen, ChevronDown } from "lucide-react";

import { slateSourcesTheme, SourcesPanel } from "@/platform/chat-core/sources";
import type { Source } from "@/platform/chat-core/types";
import { cn } from "@/platform/utils";

import Button from "../ui/Button";
import { Markdown } from "./markdown";

const renderMarkdown = (text: string) => <Markdown>{text}</Markdown>;

interface SourcesListProps {
  sources: Source[];
  messageIndex: number;
  isOpen?: boolean;
  onToggle?: () => void;
  className?: string;
}

const SourcesList = ({
  sources,
  messageIndex,
  isOpen: controlledIsOpen,
  onToggle: controlledOnToggle,
  className = "mt-4",
}: SourcesListProps) => {
  const [localIsOpen, setLocalIsOpen] = useState(false);
  const isControlled = controlledIsOpen !== undefined;
  const isOpen = isControlled ? controlledIsOpen : localIsOpen;
  const handleToggle = isControlled
    ? controlledOnToggle!
    : () => setLocalIsOpen((v) => !v);
  const sourcesContentId = `sources-${messageIndex}`;

  if (!sources || sources.length === 0) {
    return null;
  }

  return (
    <div className={className}>
      <Button
        variant="outline"
        size="sm"
        className="group"
        startIcon={
          <div className="flex items-center gap-1.5">
            <BookOpen className="size-3.5 text-indigo-600 transition-colors group-hover:text-indigo-700" />
            <ChevronDown
              className={cn(
                "size-4 text-slate-600 transition-transform duration-200",
                isOpen ? "rotate-180" : "",
              )}
            />
          </div>
        }
        onClick={handleToggle}
        title={isOpen ? "Collapse sources" : "Expand sources"}
        aria-expanded={isOpen}
        aria-controls={isControlled ? undefined : sourcesContentId}
      >
        {sources.length} {sources.length === 1 ? "Source" : "Sources"}
      </Button>

      {!isControlled && isOpen && (
        <div id={sourcesContentId}>
          <SourcesPanel
            sources={sources}
            theme={slateSourcesTheme}
            renderMarkdown={renderMarkdown}
          />
        </div>
      )}
    </div>
  );
};

export default SourcesList;
