"use client";

import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

import { cn } from "@/platform/utils";

import {
  BUTTON_VARIANTS,
  CONTROL,
  PILL_TONES,
  type ButtonVariant,
  type PillTone,
} from "./constants";
import Icon from "./Icon";

/**
 * The product's own primitives, sized and coloured from the design artifact.
 *
 * Separate from the shared `@/platform/components/ui` kit on purpose: that kit
 * is painted in literal slate/white classes, while everything here is drawn in
 * this app's theme tokens so it follows the light/dark toggle. Views that mount
 * shared feature components use both, side by side.
 */

/* ---------------------------------------------------------------- buttons */

export function Btn({
  variant = "default",
  size = "md",
  startIcon,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: "sm" | "md";
  startIcon?: ReactNode;
}) {
  return (
    <button
      type="button"
      {...rest}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-di-md font-semibold whitespace-nowrap",
        "cursor-pointer disabled:cursor-not-allowed",
        size === "sm"
          ? "px-2.5 py-1.5 text-[12.5px]"
          : "px-3.5 py-2 text-[13.5px]",
        BUTTON_VARIANTS[variant],
        className,
      )}
    >
      {startIcon}
      {children}
    </button>
  );
}

export function IconBtn({
  className,
  label,
  tooltip = true,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  /**
   * Whether the label is also shown as a hover tooltip.
   *
   * Off where the glyph already says it — a ✕ on the row it removes — because
   * the browser's own tooltip lands over the next row and follows the pointer
   * around a column of them. The accessible name stays either way.
   */
  tooltip?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={tooltip ? label : undefined}
      {...rest}
      className={cn(
        "inline-flex size-[30px] shrink-0 cursor-pointer items-center justify-center rounded-di-md",
        "border border-line bg-surface text-ink-2 hover:bg-sunken hover:text-ink",
        "disabled:cursor-not-allowed disabled:opacity-40",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function LinkBtn({
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...rest}
      className={cn(
        "inline-flex cursor-pointer items-center gap-1.5 text-[13px] font-bold text-accent hover:underline",
        className,
      )}
    >
      {children}
    </button>
  );
}

/* -------------------------------------------------------------- containers */

export function Card({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-di-lg border border-line bg-surface shadow-card",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function PageHead({
  title,
  sub,
  action,
  tight,
}: {
  title: string;
  sub?: string;
  action?: ReactNode;
  /**
   * For a screen whose subject is the thing underneath it.
   *
   * Knowledge is one table and everything above it is preamble: the same head,
   * a size down and closer to what it introduces, so the table starts in the
   * first screenful rather than halfway down it.
   */
  tight?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-start justify-between gap-5",
        tight ? "mb-3" : "mb-5",
      )}
    >
      <div>
        <h2
          className={cn(
            "m-0 font-extrabold tracking-[-0.02em] text-ink text-balance",
            tight ? "text-[21px]" : "text-[26px]",
          )}
        >
          {title}
        </h2>
        {sub && (
          <p
            className={cn(
              "m-0 mt-1 max-w-[56ch] text-ink-2",
              tight ? "text-[12.5px]" : "text-[13.5px]",
            )}
          >
            {sub}
          </p>
        )}
      </div>
      {action}
    </div>
  );
}

export function SectionLabel({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "text-[11px] font-bold tracking-[0.06em] text-ink-3 uppercase",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Hair({ className }: { className?: string }) {
  return (
    <hr className={cn("my-4 border-0 border-t border-line-soft", className)} />
  );
}

/* ------------------------------------------------------------------ pills */

export function Pill({
  tone = "neutral",
  className,
  children,
}: {
  tone?: PillTone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold",
        PILL_TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Chip({
  tone = "accent",
  className,
  children,
}: {
  tone?: "accent" | "muted";
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold whitespace-nowrap",
        tone === "accent"
          ? "bg-accent-soft text-accent-softtext"
          : "border border-line bg-sunken text-ink-2",
        className,
      )}
    >
      {children}
    </span>
  );
}

/**
 * Processing status of one document, worded as the artifact words it.
 *
 * `invalid` is a failure too: the batch route took the file but could not read
 * it, and nothing will retry it. Drawn as "Processing" it spun forever beside a
 * status strip that already counted it as failed.
 */
export function StatusPill({ status }: { status?: string | null }) {
  const value = String(status ?? "").toLowerCase();
  if (value === "completed" || value === "success" || value === "processed") {
    return (
      <Pill tone="success">
        <span className="size-1.5 rounded-full bg-success-dot" />
        Completed
      </Pill>
    );
  }
  if (value === "failed" || value === "error" || value === "invalid") {
    return (
      <Pill tone="danger">
        <span className="size-1.5 rounded-full bg-danger-dot" />
        Failed
      </Pill>
    );
  }
  return (
    <Pill tone="warn">
      <span className="di-spinner" />
      Processing
    </Pill>
  );
}

/* ------------------------------------------------------------ form fields */

export function FieldLabel({
  required,
  htmlFor,
  children,
}: {
  required?: boolean;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <label
      htmlFor={htmlFor}
      className="mb-1.5 block text-[12.5px] font-bold text-ink"
    >
      {children}
      {required && <span className="text-danger-text"> *</span>}
    </label>
  );
}

export function FieldHelp({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("mt-1.5 text-[11.5px] text-ink-2", className)}>
      {children}
    </div>
  );
}

export function Input({
  className,
  ...rest
}: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...rest} className={cn(CONTROL, className)} />;
}

export function Textarea({
  className,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...rest}
      className={cn(CONTROL, "min-h-[70px] resize-y", className)}
    />
  );
}

/**
 * `fit` sizes the control to its content instead of filling its row.
 *
 * A prop rather than a `w-auto` class from the call site: `cn` here is a plain
 * join, not `tailwind-merge`, so both widths would end up in the attribute and
 * whichever rule the stylesheet emits last would win — which is how a toolbar
 * select ended up full-width on its own line.
 */
export function Select({
  className,
  fit,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { fit?: boolean }) {
  return (
    <select
      {...rest}
      className={cn(
        fit ? CONTROL.replace("w-full", "w-auto") : CONTROL,
        "cursor-pointer",
        className,
      )}
    />
  );
}

/** The artifact's pill switch: a button, not a checkbox, with the same states. */
export function Switch({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative h-[21px] w-[38px] shrink-0 rounded-full transition-colors",
        checked ? "bg-accent" : "bg-line-strong",
        disabled ? "cursor-not-allowed opacity-55" : "cursor-pointer",
      )}
    >
      <span
        className={cn(
          "absolute top-[2.5px] left-[2.5px] size-4 rounded-full bg-white transition-transform",
          checked && "translate-x-[17px]",
        )}
      />
    </button>
  );
}

export function ToggleRow({
  label,
  desc,
  checked,
  onChange,
  disabled,
  className,
}: {
  label: string;
  desc?: ReactNode;
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3.5 py-2.5",
        className,
      )}
    >
      <div>
        <div className="text-[13px] font-bold text-ink">{label}</div>
        {desc && <div className="mt-0.5 text-[12px] text-ink-2">{desc}</div>}
      </div>
      <Switch
        checked={checked}
        onChange={onChange}
        disabled={disabled}
        label={label}
      />
    </div>
  );
}

/** A radio card, used for the answer model and the processing type. */
export function OptionCard({
  selected,
  onSelect,
  title,
  desc,
  badge,
  name,
}: {
  selected: boolean;
  onSelect: () => void;
  title: string;
  desc?: string;
  badge?: ReactNode;
  name: string;
}) {
  return (
    <label
      className={cn(
        "mb-2 flex cursor-pointer items-start gap-2.5 rounded-di-md border px-3 py-2.5",
        selected
          ? "border-accent bg-accent-soft"
          : "border-line-strong hover:border-accent",
      )}
    >
      <input
        type="radio"
        name={name}
        checked={selected}
        onChange={onSelect}
        className="mt-1 accent-[var(--di-accent)]"
      />
      <span>
        <span className="flex items-center gap-2 text-[13px] font-bold text-ink">
          {title}
          {badge}
        </span>
        {desc && (
          <span className="mt-0.5 block text-[12px] text-ink-2">{desc}</span>
        )}
      </span>
    </label>
  );
}

export function CheckRow({
  checked,
  onChange,
  label,
  desc,
  className,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  desc?: string;
  className?: string;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-2.5 py-2.5",
        className,
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 size-4 accent-[var(--di-accent)]"
      />
      <span>
        <span className="block text-[13px] font-bold text-ink">{label}</span>
        {desc && <span className="block text-[12px] text-ink-2">{desc}</span>}
      </span>
    </label>
  );
}

/** −/+ number field, as on the artifact's Temperature and Max tokens. */
export function Stepper({
  value,
  onChange,
  step = 1,
  min,
  max,
  label,
}: {
  value: number;
  onChange: (next: number) => void;
  step?: number;
  min?: number;
  max?: number;
  label: string;
}) {
  const clamp = (next: number) => {
    const rounded = Number(next.toFixed(2));
    if (min !== undefined && rounded < min) return min;
    if (max !== undefined && rounded > max) return max;
    return rounded;
  };
  return (
    <div className="flex w-fit items-center overflow-hidden rounded-di-md border border-line-strong">
      <button
        type="button"
        aria-label={`Decrease ${label}`}
        onClick={() => onChange(clamp(value - step))}
        className="h-8 w-[26px] cursor-pointer bg-sunken text-ink-2 hover:text-ink"
      >
        −
      </button>
      <input
        aria-label={label}
        value={value}
        onChange={(event) => {
          const next = Number(event.target.value);
          if (!Number.isNaN(next)) onChange(clamp(next));
        }}
        className="w-14 border-0 bg-surface p-0 text-center text-[13px] text-ink outline-none"
      />
      <button
        type="button"
        aria-label={`Increase ${label}`}
        onClick={() => onChange(clamp(value + step))}
        className="h-8 w-[26px] cursor-pointer bg-sunken text-ink-2 hover:text-ink"
      >
        +
      </button>
    </div>
  );
}

export function EmptyNote({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-di-md border border-dashed border-line-strong p-3.5 text-center text-[12.5px] text-ink-2",
        className,
      )}
    >
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ modal */

export function Modal({
  open,
  onClose,
  title,
  onBack,
  footer,
  width = "640px",
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  onBack?: () => void;
  footer?: ReactNode;
  width?: string;
  children: ReactNode;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-80 flex items-center justify-center bg-[rgba(15,17,23,0.45)] p-6"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="max-h-[88vh] w-full overflow-auto rounded-di-lg bg-surface shadow-pop"
        style={{ maxWidth: width }}
      >
        <div className="flex items-center justify-between gap-2 border-b border-line px-5 py-4">
          {onBack && (
            <IconBtn label="Back" onClick={onBack}>
              ←
            </IconBtn>
          )}
          <h3 className="m-0 flex-1 text-[17px] font-extrabold text-ink">
            {title}
          </h3>
          <IconBtn label="Close" onClick={onClose}>
            <Icon name="x" className="size-4" />
          </IconBtn>
        </div>
        <div className="px-5 py-5">{children}</div>
        {footer && (
          <div className="flex justify-end gap-2 border-t border-line px-5 py-4">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
