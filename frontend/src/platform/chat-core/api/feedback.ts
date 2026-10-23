import type { FeedbackPayload, FeedbackResponse } from "../types/feedback";

/**
 * Submits user feedback (thumbs up/down) for a chat message.
 * @param baseUrl - The API base URL (e.g., "https://app.futuresmart.ai/api")
 * @param apiKey - The API key for authentication
 * @param payload - The feedback payload
 */
export const submitFeedback = async (
  baseUrl: string,
  payload: FeedbackPayload,
): Promise<FeedbackResponse> => {
  try {
    const response = await fetch(`${baseUrl}/dashboard/v1/sessions/feedback`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error(`Feedback submission failed: ${response.status}`);
    }

    return { success: true };
  } catch (error: any) {
    return {
      success: false,
      message: error?.message || "Failed to submit feedback",
    };
  }
};
