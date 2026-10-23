"use client";

import { ChevronDown, type LucideIcon } from "lucide-react";

import { cn } from "../utils/cn";
import { useSourcesTheme } from "./context";

/**
 * Small presentational building blocks shared across the Sources panel.
 * They own layout only and read every color from the active SourcesTheme,
 * so the dashboard and widget palettes stay consistent with the design.
 */

type TagTone = "outline" | "brand" | "neutral";

export const Tag = ({
  tone = "neutral",
  children,
}: {
  tone?: TagTone;
  children: React.ReactNode;
}) => {
  const theme = useSourcesTheme();
  const tones: Record<TagTone, string> = {
    outline: theme.tagOutline,
    brand: theme.tagBrand,
    neutral: theme.chip,
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-[3px] text-[11px] font-medium leading-tight",
        tones[tone],
      )}
    >
      {children}
    </span>
  );
};

export const IconBtn = ({
  icon: Icon,
  onClick,
  label,
  active,
}: {
  icon: LucideIcon;
  onClick?: () => void;
  label: string;
  active?: boolean;
}) => {
  const theme = useSourcesTheme();
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className={cn(
        "inline-flex size-6 shrink-0 items-center justify-center rounded-md transition-colors",
        active ? theme.iconBtnActive : theme.iconBtn,
      )}
    >
      <Icon className="size-3" />
    </button>
  );
};

export const SubHeader = ({
  icon: Icon,
  iconChip,
  label,
  count,
  open,
  onToggle,
}: {
  icon: LucideIcon;
  iconChip: string;
  label: string;
  count?: number;
  open: boolean;
  onToggle: () => void;
}) => {
  const theme = useSourcesTheme();
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className={cn(
        "flex w-full items-center gap-2 rounded-lg px-3 py-[9px] text-left text-[12.5px] font-semibold transition-colors",
        theme.subHeader,
      )}
    >
      <span
        className={cn(
          "inline-flex size-[18px] shrink-0 items-center justify-center rounded-[5px]",
          iconChip,
        )}
      >
        <Icon className="size-3" strokeWidth={2.2} />
      </span>
      <span>{label}</span>
      {count != null && (
        <span
          className={cn(
            "rounded-full px-[7px] py-px text-[10.5px] font-semibold",
            theme.chip,
          )}
        >
          {count}
        </span>
      )}
      <ChevronDown
        className={cn(
          "ml-auto size-3.5 transition-transform",
          theme.textFaint,
          open && "rotate-180",
        )}
      />
    </button>
  );
};
