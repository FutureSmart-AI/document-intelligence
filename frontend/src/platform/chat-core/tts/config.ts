/**
 * TTS reuses the same fs-voice gateway and public Bearer key as STT — the key
 * already ships in the client bundle, so no new exposure and no server proxy.
 */
export { STT_API_KEY } from "../stt/config";

/** Gateway-default voice/model; the gateway resolves the concrete provider. */
export const DEFAULT_VOICE = "voice-default";
export const DEFAULT_MODEL = "tts-default";
export const DEFAULT_FORMAT: "mp3" | "wav" | "opus" = "mp3";
export const DEFAULT_SPEED = 1.0;
