import { memo } from "react";

import { Copy } from "lucide-react";

import {
  DEFAULT_POSITION,
  useToast,
  type ToastPosition,
} from "../ToastContext";
import Tooltip from "../ui/Tooltip";

interface CopyToClipboardProps {
  children: string;
  className?: string;
  name?: string;
  tooltip?: string;
  position?: ToastPosition;
}

export const CopyToClipboard = memo(
  ({
    children,
    className,
    name,
    tooltip = "Copy",
    position = DEFAULT_POSITION,
  }: CopyToClipboardProps) => {
    const { toast } = useToast();
    const copyFunction = (text: string) => {
      navigator.clipboard
        .writeText(text)
        .then(() => {
          toast.success(`${name || "Content"} copied to clipboard`, {
            position,
          });
        })
        .catch(() => {
          toast.error(`Failed to copy ${name || "Content"} to clipboard`, {
            position,
          });
        });
    };

    return (
      <Tooltip content={tooltip} side="bottom" className={className}>
        <button
          type="button"
          onClick={() => copyFunction(children)}
          className={`inline-flex cursor-pointer items-center gap-1 text-xs bg-transparent text-slate-700 hover:text-slate-900 truncate ${className || ""}`}
        >
          <Copy className="size-4" />
          {name && <span className="ml-1 truncate">{name}</span>}
        </button>
      </Tooltip>
    );
  },
);
