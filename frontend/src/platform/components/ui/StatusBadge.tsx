import { cn } from "@/platform/utils";

type BadgeVariant =
  "indigo" | "slate" | "green" | "amber" | "blue" | "purple" | "rose";

interface StatusBadgeProps {
  label: string;
  variant?: BadgeVariant;
  size?: "sm" | "md";
  className?: string;
}

const variantStyles: Record<BadgeVariant, string> = {
  indigo: "bg-indigo-100 text-indigo-700",
  slate: "bg-slate-100 text-slate-600",
  green: "bg-green-100 text-green-700",
  amber: "bg-amber-100 text-amber-700",
  blue: "bg-blue-100 text-blue-700",
  purple: "bg-purple-100 text-purple-700",
  rose: "bg-rose-100 text-rose-700",
};

const sizeStyles = {
  sm: "px-2 py-0.5 text-xs",
  md: "px-2.5 py-1 text-xs",
};

export const StatusBadge = ({
  label,
  variant = "indigo",
  size = "md",
  className = "",
}: StatusBadgeProps) => {
  return (
    <span
      className={cn(
        "rounded-full font-medium",
        variantStyles[variant],
        sizeStyles[size],
        className,
      )}
    >
      {label}
    </span>
  );
};
