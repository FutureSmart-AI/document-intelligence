import { forwardRef, memo } from "react";

import { Label } from ".";
import { X } from "lucide-react";

import { cn } from "@/platform/utils";

interface DateInputProps extends Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "onChange"
> {
  label?: string;
  description?: string;
  error?: string;
  value?: string;
  onChange?: (value: string) => void;
  clearable?: boolean;
}

const DateInput = forwardRef<HTMLInputElement, DateInputProps>(
  (
    {
      label,
      description,
      error,
      value = "",
      onChange,
      className,
      clearable = true,
      required,
      id,
      ...rest
    },
    ref,
  ) => {
    const showClear = clearable && Boolean(value);

    return (
      <div className="flex flex-col gap-2">
        {(label || description) && (
          <div className="flex flex-col gap-1">
            {label && (
              <Label htmlFor={id} required={required}>
                {label}
              </Label>
            )}
            {description && (
              <p className="text-xs text-slate-500 leading-snug">
                {description}
              </p>
            )}
          </div>
        )}
        <div className="relative">
          <input
            ref={ref}
            id={id}
            type="date"
            value={value}
            onChange={(e) => onChange?.(e.target.value)}
            className={cn(
              "w-full rounded-md border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 shadow-sm transition ring-offset-2 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:bg-slate-50",
              error ? "border-rose-400 focus:border-rose-500" : "",
              showClear ? "pr-10" : "pr-4",
              className,
            )}
            {...rest}
          />
          {showClear && (
            <button
              type="button"
              aria-label="Clear date"
              className="absolute right-2 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200"
              onClick={() => onChange?.("")}
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
        {error && <p className="text-xs text-rose-500">{error}</p>}
      </div>
    );
  },
);

DateInput.displayName = "DateInput";

export default memo(DateInput);
