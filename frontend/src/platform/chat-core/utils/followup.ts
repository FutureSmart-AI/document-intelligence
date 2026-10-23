import { get, isArray, isEmpty } from "lodash-es";

/**
 * Extracts follow-up questions from a message object.
 * Checks `follow_up`, `followUp`, and `debug_info.follow_up` in priority order.
 * Returns an empty array when none are found.
 */
export const extractFollowUpQuestions = (message: any): string[] => {
  if (!message) return [];
  const followUp =
    get(message, "follow_up") ||
    get(message, "followUp") ||
    get(message, "debug_info.follow_up") ||
    null;
  if (followUp && isArray(followUp) && !isEmpty(followUp)) {
    return followUp as string[];
  }
  return [];
};
