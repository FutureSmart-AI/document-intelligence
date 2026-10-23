import { get } from "lodash-es";

/** One question and the answer under it, as the thread renders them. */
export interface ThreadTurn {
  key: string;
  question: string;
  /** Whether the question was dictated rather than typed. */
  questionViaVoice: boolean;
  /** Null while a question is still waiting for its assistant message. */
  answer: Record<string, unknown> | null;
}

const text = (message: unknown): string =>
  String(get(message, "response") ?? get(message, "message") ?? "");

/**
 * Pairs the flat message list into question/answer turns.
 *
 * The stream and the stored session both hand back alternating user/assistant
 * messages. An assistant message with no question before it (which a reloaded
 * thread can start with) still gets a turn, so no answer is dropped.
 */
export function toTurns(messages: unknown[]): ThreadTurn[] {
  const turns: ThreadTurn[] = [];

  for (let index = 0; index < messages.length; index++) {
    const message = messages[index] as Record<string, unknown>;
    if (!message) continue;
    const role = get(message, "role");

    if (role === "user") {
      const next = messages[index + 1] as Record<string, unknown> | undefined;
      const answer = get(next, "role") === "assistant" ? next : null;
      if (answer) index++;
      turns.push({
        key: String(
          get(answer, "chat_id") ?? get(message, "chat_id") ?? `turn-${index}`,
        ),
        question: text(message),
        questionViaVoice: Boolean(get(message, "via_voice")),
        answer: answer ?? null,
      });
    } else if (role === "assistant") {
      turns.push({
        key: String(get(message, "chat_id") ?? `turn-${index}`),
        question: "",
        questionViaVoice: false,
        answer: message,
      });
    }
  }

  return turns;
}
