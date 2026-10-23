import React, { forwardRef, memo } from "react";

import { cn } from "@/platform/utils";

type IconButtonVariant = "ghost" | "outline" | "danger-ghost";
type IconButtonSize = "xs" | "sm" | "md";

export interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: IconButtonVariant;
  size?: IconButtonSize;
  icon: React.ReactNode;
  badge?: React.ReactNode;
}

const variantClasses: Record<IconButtonVariant, string> = {
  ghost: "text-slate-400 hover:bg-slate-200/70 hover:text-slate-600",
  outline:
    "border border-slate-200 bg-slate-50 text-slate-500 hover:bg-slate-100 hover:text-slate-700",
  "danger-ghost": "text-slate-400 hover:bg-rose-100 hover:text-rose-600",
};

const sizeClasses: Record<IconButtonSize, string> = {
  xs: "p-0.5 [&>svg]:size-3",
  sm: "p-1 [&>svg]:size-3.5",
  md: "p-1.5 [&>svg]:size-4",
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton(
    { variant = "ghost", size = "sm", icon, badge, className, ...rest },
    ref,
  ) {
    return (
      <button
        ref={ref}
        type="button"
        className={cn(
          "relative cursor-pointer p-2 inline-flex items-center justify-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40 disabled:opacity-50 disabled:pointer-events-none",
          variantClasses[variant],
          sizeClasses[size],
          className,
        )}
        {...rest}
      >
        {icon}
        {badge}
      </button>
    );
  },
);

IconButton.displayName = "IconButton";

export default memo(IconButton);
