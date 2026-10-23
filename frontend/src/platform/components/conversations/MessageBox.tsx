import {
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";

import { ArrowUp, Loader2, Mic, Square, X } from "lucide-react";

import {
  createTranscriber,
  DEFAULT_PREFERRED_LANGUAGES,
  formatElapsed,
  sttErrorMessage,
  useStreamingSpeechToText,
} from "@/platform/chat-core/stt";
import { useToast } from "@/platform/components/ToastContext";
import { sttMicEnabled, sttUrl } from "@/platform/config";
import { useAuth } from "@/platform/hooks/useAuth";

import { Button } from "../ui";
import Tooltip from "../ui/Tooltip";

type ChatInputBoxProps = {
  inputValue: string;
  setInputValue: (v: string) => void;
  getBotAnswer: (meta?: { viaVoice?: boolean }) => void;
  /** Explicit product setting; omitted preserves the chat registry behaviour. */
  voiceInputEnabled?: boolean;
  ref: React.RefObject<{ focus: () => void }>;
};

const ChatInputBox = ({
  inputValue,
  setInputValue,
  getBotAnswer,
  voiceInputEnabled,
  ref,
}: ChatInputBoxProps) => {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const hintId = "chat-input-hint";
  // True right after a voice transcript lands; clears once the user types.
  const [fromVoice, setFromVoice] = useState(false);

  const { toast } = useToast();
  const { selectedAgent } = useAuth();
  // Per-agent voice config (read defensively — falls back to the global env
  // flag when the agent params haven't been persisted by the backend yet).
  const agentParams = selectedAgent?.config?.agent_params;
  // Per-agent language wins; otherwise send the ordered preferred list
  // (English primary, Hindi accepted fallback) so the gateway honours it.
  const voiceLanguage =
    agentParams?.default_language ?? DEFAULT_PREFERRED_LANGUAGES;

  // One transcriber per language. Routes through the proxy `/stt` so the
  // gateway key stays server-side.
  const transcribe = useMemo(
    () =>
      createTranscriber({
        baseUrl: sttUrl,
        withCredentials: true,
        language: voiceLanguage,
      }),
    [voiceLanguage],
  );

  const focusInputEnd = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  };

  const stt = useStreamingSpeechToText({
    realtime: {
      baseUrl: sttUrl,
      withCredentials: true,
      language: voiceLanguage,
    },
    transcribe,
    onTranscript: (text) => {
      setInputValue(inputValue ? `${inputValue.trim()} ${text}` : text);
      setFromVoice(true);
      // Wait a tick for the textarea to refill before placing the cursor.
      setTimeout(focusInputEnd, 0);
    },
  });

  const micAvailable =
    sttMicEnabled &&
    (voiceInputEnabled ?? agentParams?.voice_input ?? true) &&
    stt.isSupported;
  const showPermissionModal = stt.errorKind === "permission-denied";

  // Hybrid error UX: transient failures auto-dismiss as a toast; the
  // permission-denied case keeps its modal (it needs actionable steps).
  useEffect(() => {
    if (
      stt.status === "error" &&
      stt.errorKind &&
      stt.errorKind !== "permission-denied"
    ) {
      toast.error(sttErrorMessage(stt.errorKind), { duration: 4000 });
      stt.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stt.status, stt.errorKind]);

  const autoResize = (el: HTMLTextAreaElement | null) => {
    if (!el) return;
    el.style.height = "0px";
    const next = Math.min(160, el.scrollHeight);
    el.style.height = `${next}px`;
  };

  useImperativeHandle(ref, () => ({
    focus: () => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(
        inputValue.length,
        inputValue.length,
      );
    },
  }));

  useEffect(() => {
    autoResize(textareaRef.current);
  }, [inputValue]);

  const handleSend = () => {
    const viaVoice = fromVoice;
    setFromVoice(false);
    getBotAnswer({ viaVoice });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (!e.shiftKey || e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      if (inputValue.trim() !== "") {
        handleSend();
      }
    }
  };

  const toggleRecording = () => {
    if (stt.isRecording) {
      void stt.stop();
    } else {
      void stt.start();
    }
  };

  const isBusyRecording = stt.isRecording || stt.status === "transcribing";

  const wrapperClass = stt.isRecording
    ? "border-red-300 ring-red-400/40"
    : fromVoice
      ? "border-emerald-300 ring-emerald-400/40"
      : "border-slate-200 ring-black/5 focus-within:ring-indigo-500/40 hover:border-slate-300";

  return (
    <>
      <div
        className={`rounded-2xl border bg-white shadow-sm ring-1 transition focus-within:ring-2 ${wrapperClass}`}
      >
        <div className="flex items-end gap-2 px-3 py-2">
          {stt.isRecording ? (
            <div
              className="flex min-w-0 flex-1 items-center gap-3 px-1 py-2"
              role="status"
              aria-live="polite"
            >
              <span className="relative flex size-2.5">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-red-400 opacity-75" />
                <span className="relative inline-flex size-2.5 rounded-full bg-red-500" />
              </span>
              <div className="flex h-5 items-end gap-0.5" aria-hidden="true">
                {[0, 120, 240, 360, 200].map((delay, i) => (
                  <span
                    key={i}
                    className="stt-wave-bar w-0.5 rounded-full bg-red-400"
                    style={{ height: "100%", animationDelay: `${delay}ms` }}
                  />
                ))}
              </div>
              <span className="text-sm tabular-nums text-slate-600">
                {formatElapsed(stt.elapsedMs)}
              </span>
              {stt.interimTranscript ? (
                <span className="max-h-20 min-w-0 flex-1 overflow-y-auto whitespace-pre-wrap break-words text-sm italic text-slate-500">
                  {stt.interimTranscript}
                </span>
              ) : (
                <span className="text-xs text-slate-400">
                  Recording… tap stop when done
                </span>
              )}
            </div>
          ) : stt.status === "transcribing" ? (
            <div
              className="flex min-w-0 flex-1 items-center gap-2.5 px-1 py-3 text-sm text-slate-500"
              role="status"
              aria-live="polite"
            >
              <Loader2 className="size-4 shrink-0 animate-spin" />
              {stt.interimTranscript ? (
                <span className="max-h-20 min-w-0 flex-1 overflow-y-auto whitespace-pre-wrap break-words italic">
                  {stt.interimTranscript}
                </span>
              ) : (
                <>
                  <span
                    className="h-2.5 w-28 animate-pulse rounded-full bg-gradient-to-r from-slate-200 via-slate-100 to-slate-200"
                    aria-hidden="true"
                  />
                  <span>Transcribing…</span>
                </>
              )}
            </div>
          ) : (
            <textarea
              data-demo="chat-input"
              ref={textareaRef}
              rows={1}
              aria-label="Message"
              aria-multiline="true"
              aria-describedby={hintId}
              placeholder="Send a message…"
              value={inputValue}
              onChange={(e) => {
                if (fromVoice) setFromVoice(false);
                setInputValue((e.target as HTMLTextAreaElement).value);
              }}
              onKeyDown={handleKeyDown}
              onInput={(e) => autoResize(e.target as HTMLTextAreaElement)}
              spellCheck
              className="flex-1 max-h-40 w-full resize-none bg-transparent px-1 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none"
            />
          )}

          {inputValue.trim().length > 0 && !isBusyRecording && (
            <Tooltip content="Clear" side="top" className="w-auto">
              <Button
                onClick={() => {
                  setInputValue("");
                  setFromVoice(false);
                  textareaRef.current?.focus();
                }}
                aria-label="Clear message"
                shape="rounded"
                size="icon"
                className="bg-transparent text-slate-500! hover:bg-slate-50"
                startIcon={<X className="size-4" />}
              />
            </Tooltip>
          )}

          {stt.isRecording && (
            <Tooltip content="Discard recording" side="top" className="w-auto">
              <Button
                onClick={() => stt.cancel()}
                aria-label="Discard recording"
                shape="rounded"
                size="icon"
                className="bg-transparent text-slate-500! hover:bg-slate-50"
                startIcon={<X className="size-4" />}
              />
            </Tooltip>
          )}

          {micAvailable && (
            <Tooltip
              content={
                stt.isRecording ? "Stop recording" : "Record voice message"
              }
              side="top"
              className="w-auto"
            >
              <Button
                onClick={toggleRecording}
                disabled={
                  stt.status === "requesting" || stt.status === "transcribing"
                }
                aria-label={
                  stt.isRecording ? "Stop recording" : "Record voice message"
                }
                aria-pressed={stt.isRecording}
                shape="rounded"
                size="icon"
                className={
                  stt.isRecording
                    ? "bg-red-500! text-white hover:bg-red-600!"
                    : "bg-[#F1F3F4] text-slate-600! hover:bg-indigo-50 hover:text-indigo-600!"
                }
                startIcon={
                  stt.status === "requesting" ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : stt.isRecording ? (
                    <Square className="size-4 fill-current" />
                  ) : (
                    <Mic className="size-4 text-white" />
                  )
                }
              />
            </Tooltip>
          )}

          <Tooltip
            content="Send (Enter or Cmd/Ctrl+Enter)"
            side="top"
            className="w-auto"
          >
            <Button
              onClick={
                inputValue.trim() && !isBusyRecording ? handleSend : undefined
              }
              disabled={!inputValue.trim() || isBusyRecording}
              shape="rounded"
              size="icon"
              aria-label="Send message"
              data-demo="send"
              startIcon={<ArrowUp className="size-4" />}
            />
          </Tooltip>
        </div>

        <div className="flex items-center justify-between px-3 pb-2">
          {fromVoice && !stt.isRecording ? (
            <div className="text-[10px] text-emerald-600">
              via voice · tap to edit before sending
            </div>
          ) : (
            <div id={hintId} className="text-[10px] text-slate-400">
              Enter to send • Shift+Enter for new line • Cmd/Ctrl+Enter to send
            </div>
          )}
          {inputValue.length > 0 ? (
            <div className="text-[10px] text-slate-400">
              {inputValue.length}
            </div>
          ) : null}
        </div>
      </div>

      {/* Transient errors (network, no-speech, too-short, device) surface as
          auto-dismiss toasts via the effect above; only the permission case
          keeps a modal. */}
      {showPermissionModal && (
        <MicPermissionModal onClose={() => stt.reset()} />
      )}
    </>
  );
};

const MicPermissionModal = ({ onClose }: { onClose: () => void }) => (
  <div
    className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    role="dialog"
    aria-modal="true"
    aria-labelledby="mic-permission-title"
    onClick={onClose}
  >
    <div
      className="w-full max-w-sm rounded-2xl bg-white p-5 shadow-xl"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="mb-3 flex items-center gap-2">
        <span className="flex size-9 items-center justify-center rounded-full bg-red-50 text-red-500">
          <Mic className="size-5" />
        </span>
        <h2
          id="mic-permission-title"
          className="text-base font-semibold text-slate-900"
        >
          Microphone access needed
        </h2>
      </div>
      <p className="mb-3 text-sm text-slate-600">
        {sttErrorMessage("permission-denied")}
      </p>
      <ol className="mb-4 list-decimal space-y-1 pl-5 text-sm text-slate-600">
        <li>
          Click the lock / site-settings icon in your browser's address bar.
        </li>
        <li>Set Microphone to “Allow”.</li>
        <li>Reload the page and try recording again.</li>
      </ol>
      <div className="flex justify-end">
        <Button onClick={onClose} aria-label="Close">
          Got it
        </Button>
      </div>
    </div>
  </div>
);

export default ChatInputBox;
