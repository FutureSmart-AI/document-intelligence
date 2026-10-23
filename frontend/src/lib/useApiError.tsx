"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";

import { useToast } from "@/platform/components/ToastContext";

import { apiMessage } from "./apiError";
import { breachSentence, quotaErrorOf } from "./quotaError";
import { LIMITS_TAB } from "./routes";

/**
 * Reporting a failed write, in the one place that knows what to do with each
 * kind.
 *
 * Two kinds arrive. Most are a sentence — `apiMessage` finds it and the toast
 * says it. A quota refusal is not: the account is full, so the useful part is
 * not the sentence but the way out of it, and this toast carries a link to the
 * Limits tab where the allowance and its ceiling are shown. Without it, a
 * reader is told "you have reached one of your limits" with nowhere to go and
 * no way to see which.
 *
 * Both are keyed, so a person clicking Create three times against a full
 * account gets one toast rather than a stack of the same sentence.
 */
export function useApiError() {
  const { toast } = useToast();
  const router = useRouter();

  return useCallback(
    (error: unknown, fallback: string) => {
      const quota = quotaErrorOf(error);
      if (!quota) {
        toast.error(apiMessage(error, fallback), { key: "di-api-error" });
        return;
      }

      const detail = breachSentence(quota);
      toast.open({
        type: "error",
        key: "di-quota",
        // Long enough to read two sentences and reach the link; the default
        // three seconds is not.
        duration: 12000,
        content: (
          <span
            data-demo="quota-toast"
            className="flex flex-wrap items-center gap-x-2 gap-y-1"
          >
            <span>
              {quota.message}
              {detail ? ` ${detail}` : ""}
            </span>
            <button
              type="button"
              data-demo="quota-limits-link"
              className="shrink-0 font-bold underline underline-offset-2"
              onClick={() => {
                toast.destroy("di-quota");
                router.push(LIMITS_TAB);
              }}
            >
              View plan limits
            </button>
          </span>
        ),
      });
    },
    [router, toast],
  );
}
