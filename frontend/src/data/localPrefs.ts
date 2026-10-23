"use client";

/**
 * The one per-collection choice this app keeps in the browser.
 *
 * **The voice input language** has no field anywhere on the service: the chat
 * config carries `voice_input_enabled` but no language, and the service has no
 * speech pipeline to send one to. It lives in `localStorage` rather than being
 * posted to a route that would ignore it (or 422 on an unknown key). When the
 * service grows a place for it, this module is the only place that has to
 * change.
 *
 * **Document processing** used to live here too — the mode was a real field
 * (`document_processing_mode`) sent per upload, so the choice was remembered
 * between one upload and the next. The service now detects it itself, including
 * re-reading an image-only PDF, so the control and the value it remembered are
 * both gone. (product review, 2026-09-01.)
 * Anything a browser still has stored under `docProcessing` is simply never
 * read again.
 */
const KEY = "di-local-prefs";

interface Prefs {
  voiceLanguage?: string;
}

type Store = Record<string, Prefs>;

function read(): Store {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Store) : {};
  } catch {
    return {};
  }
}

function write(knowledgeId: string, patch: Prefs): void {
  if (typeof window === "undefined") return;
  try {
    const store = read();
    store[knowledgeId] = { ...store[knowledgeId], ...patch };
    window.localStorage.setItem(KEY, JSON.stringify(store));
  } catch {
    // A browser with storage disabled keeps the defaults; nothing else depends
    // on these values, so failing to persist them is not worth surfacing.
  }
}

/** "" means auto-detect, which is also the default. */
export function getVoiceLanguage(knowledgeId: string | null): string {
  if (!knowledgeId) return "";
  return read()[knowledgeId]?.voiceLanguage ?? "";
}

export function setVoiceLanguage(
  knowledgeId: string | null,
  value: string,
): void {
  if (!knowledgeId) return;
  write(knowledgeId, { voiceLanguage: value });
}
