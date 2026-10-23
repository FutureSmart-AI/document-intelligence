"use client";

import React, { useEffect, useRef, useState } from "react";

import { cn } from "@/platform/utils";

type DropdownAlign = "left" | "right";

interface DropdownMenuItemProps {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  icon?: React.ReactNode;
  className?: string;
}

interface DropdownMenuProps {
  trigger: React.ReactNode;
  items: DropdownMenuItemProps[];
  align?: DropdownAlign;
  className?: string;
}

export function DropdownMenuItem({
  label,
  onClick,
  disabled = false,
  icon,
  className,
}: DropdownMenuItemProps) {
  return (
    <button
      type="button"
      className={cn(
        "flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 disabled:pointer-events-none disabled:opacity-50",
        className,
      )}
      onClick={onClick}
      disabled={disabled}
    >
      {icon}
      {label}
    </button>
  );
}

export function DropdownMenu({
  trigger,
  items,
  align = "right",
  className,
}: DropdownMenuProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const handleClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  return (
    <div className={cn("relative", className)} ref={ref}>
      <div onClick={() => setOpen((prev) => !prev)}>{trigger}</div>
      {open && (
        <div
          className={cn(
            "absolute top-full z-20 mt-1 min-w-40 rounded-lg border border-slate-200 bg-white py-1 shadow-lg",
            align === "right" ? "right-0" : "left-0",
          )}
        >
          {items.map((item) => (
            <DropdownMenuItem
              key={item.label}
              {...item}
              onClick={() => {
                item.onClick();
                setOpen(false);
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
