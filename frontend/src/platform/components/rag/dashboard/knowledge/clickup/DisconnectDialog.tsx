import { useState } from "react";

import { Button, Dialog } from "@/platform/components/ui";
import { cn } from "@/platform/utils";

import { useDisconnectClickUp } from "./hooks/useClickUpApi";

interface DisconnectDialogProps {
  open: boolean;
  onClose: () => void;
  /** Called after a successful disconnect. */
  onDisconnected: () => void;
}

/** Danger-zone confirm: keep or purge the indexed docs when disconnecting. */
export const DisconnectDialog = ({
  open,
  onClose,
  onDisconnected,
}: DisconnectDialogProps) => {
  const [purgeDocs, setPurgeDocs] = useState(false);
  const disconnect = useDisconnectClickUp();

  const handleConfirm = async () => {
    await disconnect.mutateAsync({ purgeDocs });
    onDisconnected();
    onClose();
  };

  const Option = ({
    selected,
    onClick,
    title,
    desc,
    danger,
  }: {
    selected: boolean;
    onClick: () => void;
    title: string;
    desc: string;
    danger?: boolean;
  }) => (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-start gap-2.5 rounded-lg border px-3 py-2.5 text-left transition-colors",
        selected
          ? "border-indigo-500 bg-indigo-50/50"
          : "border-slate-200 hover:border-slate-300",
      )}
    >
      <span
        className={cn(
          "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border",
          selected ? "border-indigo-600" : "border-slate-300",
        )}
      >
        {selected && <span className="size-2 rounded-full bg-indigo-600" />}
      </span>
      <span>
        <span
          className={cn(
            "block text-sm font-medium",
            danger ? "text-rose-600" : "text-slate-900",
          )}
        >
          {title}
        </span>
        <span className="mt-0.5 block text-xs text-slate-500">{desc}</span>
      </span>
    </button>
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      widthClass="max-w-md"
      title="Disconnect ClickUp"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={handleConfirm}
            loading={disconnect.isPending}
            loadingText="Disconnecting…"
          >
            Disconnect
          </Button>
        </>
      }
    >
      <p className="mb-4 text-sm text-slate-600">
        What would you like to do with the already-indexed ClickUp documents?
      </p>
      <div className="space-y-2">
        <Option
          selected={!purgeDocs}
          onClick={() => setPurgeDocs(false)}
          title="Keep indexed docs"
          desc="Documents remain in your knowledge base. No new syncs will happen."
        />
        <Option
          selected={purgeDocs}
          onClick={() => setPurgeDocs(true)}
          title="Delete all ClickUp docs from index"
          desc="All ClickUp-sourced documents are permanently removed. This cannot be undone."
          danger
        />
      </div>
    </Dialog>
  );
};
