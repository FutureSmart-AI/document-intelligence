import { cn } from "@/platform/utils";

/**
 * The product mark: a document with a check badge, drawn from the artifact.
 *
 * `currentColor` is not used for the plate because the mark keeps its accent in
 * both themes — the tokens it references are re-defined per theme, so it darkens
 * and lightens with the rest of the page instead of inverting.
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg
      className={cn("size-[26px] shrink-0", className)}
      viewBox="0 0 26 26"
      fill="none"
      aria-hidden="true"
    >
      <rect
        x="4"
        y="2"
        width="15"
        height="22"
        rx="2.5"
        fill="var(--di-accent-soft)"
        stroke="var(--di-accent)"
        strokeWidth="1.4"
      />
      <path
        d="M8 8h7M8 12h7M8 16h4"
        stroke="var(--di-accent)"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <circle cx="19.5" cy="18.5" r="4.5" fill="var(--di-accent)" />
      <path
        d="M17.7 18.5l1.2 1.2 2.2-2.4"
        stroke="#fff"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Wordmark — "Document" in the text colour, "Intelligence" in the accent. */
export function BrandWord({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "text-[14.5px] font-bold tracking-[0.1px] whitespace-nowrap text-ink",
        className,
      )}
    >
      Document <b className="font-extrabold text-accent">Intelligence</b>
    </span>
  );
}
