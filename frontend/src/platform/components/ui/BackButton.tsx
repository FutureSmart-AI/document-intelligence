"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";

import { ArrowLeft } from "lucide-react";

import { cn } from "@/platform/utils";

import Button from "./Button";

type BackButtonProps = {
  /** URL to navigate to. If not provided, uses router.back() */
  href?: string;
  /** Custom onClick handler. Overrides href behavior */
  onClick?: () => void;
  /** Button label. Defaults to "Back" */
  label?: string;
  /** Whether the button should be sticky at top */
  sticky?: boolean;
  /** Additional className for the button */
  className?: string;
  /** Additional className for the wrapper */
  wrapperClassName?: string;
};

/**
 * Reusable back navigation button with consistent styling
 */
const BackButton = ({
  href,
  onClick,
  label = "Back",
  sticky = false,
  className = "",
  wrapperClassName = "",
  children,
}: React.PropsWithChildren<BackButtonProps>) => {
  const router = useRouter();

  const handleClick = useCallback(() => {
    if (onClick) {
      onClick();
    } else if (href) {
      router.push(href);
    } else {
      router.back();
    }
  }, [onClick, href, router]);

  const button = children ? (
    <button
      type="button"
      onClick={handleClick}
      className={cn(
        "inline-flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-slate-600 hover:bg-slate-100 hover:text-slate-900 transition-colors",
        className,
      )}
    >
      <ArrowLeft className="size-4 shrink-0" />
      {children}
    </button>
  ) : (
    <Button
      variant="ghost"
      size="sm"
      startIcon={<ArrowLeft className="size-4" />}
      onClick={handleClick}
      className={className}
    >
      {label}
    </Button>
  );

  if (sticky) {
    return (
      <div
        className={cn(
          "sticky top-0 z-10 mb-4 border-b border-slate-100 bg-white/95 py-3 backdrop-blur-sm",
          wrapperClassName,
        )}
      >
        {button}
      </div>
    );
  }

  return button;
};

export default BackButton;
