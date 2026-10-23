import React, { forwardRef, memo } from "react";

export type LabelSize = "xs" | "sm" | "md" | "lg";

export interface LabelProps extends React.LabelHTMLAttributes<HTMLLabelElement> {
  size?: LabelSize;
  required?: boolean;
  className?: string;
  children: React.ReactNode;
}

const sizeClasses: Record<LabelSize, string> = {
  xs: "text-xs",
  sm: "text-sm",
  md: "text-base",
  lg: "text-lg",
};

const baseClasses = "font-medium text-slate-700";

const Label = forwardRef<HTMLLabelElement, LabelProps>(
  (
    { size = "sm", required = false, className = "", children, ...props },
    ref,
  ) => {
    const labelClasses =
      `${baseClasses} ${sizeClasses[size]} ${className}`.trim();

    return (
      <label ref={ref} className={labelClasses} {...props}>
        {children}
        {required && (
          <span className="ml-1 text-rose-500" aria-label="required">
            *
          </span>
        )}
      </label>
    );
  },
);

Label.displayName = "Label";

export default memo(Label);
