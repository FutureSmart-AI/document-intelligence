import React, { forwardRef, memo } from "react";
import Link from "next/link";

import { Loader2 } from "lucide-react";

import { cn } from "@/platform/utils";

type ButtonVariant =
  "primary" | "secondary" | "outline" | "danger" | "ghost" | "link";

type ButtonSize = "xs" | "sm" | "md" | "lg" | "icon";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  startIcon?: React.ReactNode;
  endIcon?: React.ReactNode;
  loading?: boolean;
  loadingText?: string;
  fullWidth?: boolean;
  as?: "button" | "a" | "span";
  className?: string;
  style?: React.CSSProperties;
  shape?: "square" | "rounded";
  href?: string;
  title?: string;
  labelClassName?: string;
  target?: "_blank" | "_self" | "_top" | "_parent";
}

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    "bg-indigo-600 text-white hover:bg-indigo-700 disabled:hover:bg-indigo-600",
  secondary:
    "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50",
  outline:
    "border border-slate-300 bg-transparent text-slate-700 hover:bg-slate-50",
  danger: "bg-rose-600 text-white hover:bg-rose-700 disabled:hover:bg-rose-600",
  ghost: "bg-transparent text-slate-700 hover:bg-slate-50",
  link: "bg-transparent text-indigo-600 hover:underline px-0",
};

const sizeClasses: Record<ButtonSize, string> = {
  xs: "px-2 py-1 text-xs",
  sm: "px-3 py-1.5 text-sm",
  md: "px-4 py-2 text-sm",
  lg: "px-5 py-2.5 text-base",
  icon: "h-9 w-9 p-0",
};

export const Button = forwardRef<
  HTMLButtonElement & HTMLAnchorElement,
  ButtonProps
>(function Button(
  {
    variant = "primary",
    size = "md",
    startIcon,
    endIcon,
    loading = false,
    loadingText,
    fullWidth = false,
    as = "button",
    className,
    children,
    disabled,
    shape = "square",
    labelClassName = "_self",
    target = "",
    ...rest
  },
  ref,
) {
  const isDisabled = disabled || loading;

  const baseClasses =
    "inline-flex cursor-pointer items-center justify-center gap-2 rounded-md font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40 disabled:opacity-60 disabled:cursor-not-allowed";

  const classes = cn(
    baseClasses,
    sizeClasses[size],
    variantClasses[variant],
    fullWidth ? "w-full" : undefined,
    shape === "square" ? "rounded-lg" : "rounded-full! p-0! w-7! h-7!",
    className,
    loading ? "cursor-wait!" : undefined,
  );

  const content = (
    <>
      {/* Start icon or spinner */}
      {loading ? (
        <Loader2
          className={cn(
            "h-4 w-4 animate-spin",
            // Ensure good contrast for all variants
            variant === "secondary" ||
              variant === "outline" ||
              variant === "ghost"
              ? "text-slate-700"
              : variant === "link"
                ? "text-indigo-600"
                : "text-white",
          )}
          aria-hidden="true"
        />
      ) : (
        startIcon && (
          <span className="shrink-0 [&>svg]:size-4">{startIcon}</span>
        )
      )}

      {/* Label */}
      {children && (
        <span className={`whitespace-nowrap ${labelClassName}`}>
          {loading && loadingText ? loadingText : children}
        </span>
      )}

      {/* End icon */}
      {!loading && endIcon ? (
        <span className="shrink-0 [&>svg]:size-4">{endIcon}</span>
      ) : null}
    </>
  );

  if (as === "a") {
    // TypeScript: safer to assert `rest` as `unknown` first, then cast to anchor props
    const anchorProps =
      rest as unknown as React.AnchorHTMLAttributes<HTMLAnchorElement>;
    return (
      <Link
        ref={ref as React.Ref<HTMLAnchorElement>}
        href={anchorProps.href}
        className={cn(classes, isDisabled ? "pointer-events-none" : undefined)}
        aria-disabled={isDisabled || undefined}
        aria-busy={loading || undefined}
        target={target}
        {...anchorProps}
      >
        {content}
      </Link>
    );
  }

  if (as === "span") {
    const spanProps = rest as React.HTMLAttributes<HTMLSpanElement>;
    return (
      <span
        ref={ref as React.Ref<HTMLSpanElement>}
        className={cn(classes, isDisabled ? "pointer-events-none" : undefined)}
        aria-busy={loading || undefined}
        {...spanProps}
      >
        {content}
      </span>
    );
  }

  const buttonProps = rest as React.ButtonHTMLAttributes<HTMLButtonElement>;

  const buttonComp = (
    <button
      ref={ref}
      className={classes}
      aria-busy={loading || undefined}
      disabled={isDisabled}
      type={buttonProps.type ?? "button"}
      {...buttonProps}
    >
      {content}
    </button>
  );

  // if (title) {
  //   return <Tooltip content={title ?? ""}>{buttonComp}</Tooltip>;
  // }

  return buttonComp;
});

Button.displayName = "Button";

export default memo(Button);
