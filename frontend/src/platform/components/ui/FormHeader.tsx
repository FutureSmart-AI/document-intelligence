import React from "react";

import { StatusBadge } from "./StatusBadge";

interface FormHeaderProps {
  icon?: React.ReactNode;
  title: string;
  badge?: {
    label: string;
    variant?:
      "indigo" | "slate" | "green" | "amber" | "blue" | "purple" | "rose";
  };
  description?: string;
  className?: string;
}

export const FormHeader = ({
  icon,
  title,
  badge,
  description,
  className = "",
}: FormHeaderProps) => {
  return (
    <div
      className={`flex flex-col gap-4 border-b border-slate-200 pb-6 ${className}`}
    >
      <div className="flex items-start gap-3">
        {icon && <div className="flex-shrink-0">{icon}</div>}
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-semibold text-slate-900">{title}</h3>
            {badge && (
              <StatusBadge
                label={badge.label}
                variant={badge.variant || "indigo"}
              />
            )}
          </div>
          {description && (
            <p className="mt-1 text-sm text-slate-600">{description}</p>
          )}
        </div>
      </div>
    </div>
  );
};
