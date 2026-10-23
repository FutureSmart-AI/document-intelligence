export interface FeedbackPayload {
  chat_id: string;
  session_id: string;
  feedback: "thumbs_up" | "thumbs_down";
}

export interface FeedbackResponse {
  success: boolean;
  message?: string;
}
