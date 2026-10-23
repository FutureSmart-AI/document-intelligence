"use client";

import { useRef } from "react";

import MessageBox from "@/platform/components/conversations/MessageBox";

interface DiComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: (meta?: { viaVoice?: boolean }) => void;
  voiceInputEnabled?: boolean;
}

/**
 * The composer, in the artifact's frame.
 *
 * The input itself is the agent UI's `MessageBox`, as on the Database Agent's
 * screen: it carries the streaming speech-to-text stack, and re-deriving that
 * here would quietly drop behaviour that took several fixes to get right.
 */
export default function DiComposer({
  value,
  onChange,
  onSend,
  voiceInputEnabled,
}: DiComposerProps) {
  const inputRef = useRef<{ focus: () => void }>({ focus: () => {} });

  return (
    <div className="flex-none px-6 pt-3.5 pb-5">
      <div className="mx-auto flex max-w-205 flex-col gap-2">
        <MessageBox
          ref={inputRef}
          inputValue={value}
          setInputValue={onChange}
          getBotAnswer={onSend}
          voiceInputEnabled={voiceInputEnabled}
        />
        {/* The keyboard hint is `MessageBox`'s own. */}
        <div className="text-[11px] text-ink-3">
          Answers are drawn from your documents. Check the passages before
          acting on them.
        </div>
      </div>
    </div>
  );
}
