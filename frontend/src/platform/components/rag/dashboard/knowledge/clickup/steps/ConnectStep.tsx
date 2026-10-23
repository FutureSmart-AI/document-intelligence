import { Check } from "lucide-react";

import { Input } from "@/platform/components/ui";

import type { ClickUpWizardState } from "../hooks/useClickUpWizardState";

interface ConnectStepProps {
  wizard: ClickUpWizardState;
}

/** Step 1 — collect ClickUp credentials. Validation happens on "Next" (POST /connect). */
export const ConnectStep = ({ wizard }: ConnectStepProps) => {
  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <label className="text-sm font-medium text-slate-700">
          ClickUp API Key <span className="text-rose-600">*</span>
        </label>
        <Input
          type="password"
          fullWidth
          value={wizard.apiToken}
          onChange={(v) => {
            wizard.setApiToken(v);
            wizard.setIsConnected(false);
          }}
          placeholder="pk_••••••••••••••••••••••••"
        />
        <p className="text-xs text-slate-500">
          Personal API token — ClickUp Settings → Apps → Generate
        </p>
      </div>

      <div className="space-y-1.5">
        <label className="text-sm font-medium text-slate-700">
          Workspace ID <span className="text-rose-600">*</span>
        </label>
        <Input
          fullWidth
          value={wizard.workspaceId}
          onChange={(v) => {
            wizard.setWorkspaceId(v);
            wizard.setIsConnected(false);
          }}
          placeholder="e.g. 1234567890"
        />
        <p className="text-xs text-slate-500">
          In the URL: app.clickup.com/<b>1234567890</b>/home
        </p>
      </div>

      <div className="space-y-1.5">
        <label className="text-sm font-medium text-slate-700">
          Space ID <span className="text-slate-400">(optional)</span>
        </label>
        <Input
          fullWidth
          value={wizard.spaceIdsInput}
          onChange={wizard.setSpaceIdsInput}
          placeholder="e.g. 9876543210"
        />
        <p className="text-xs text-slate-500">
          The space containing your docs. Comma-separate for multiple spaces.
          Leave blank to index the whole workspace.
        </p>
      </div>

      {wizard.isConnected && (
        <div className="inline-flex items-center gap-1.5 rounded-md bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700">
          <Check className="size-3.5" />
          Credentials verified — {wizard.spaces.length} space
          {wizard.spaces.length === 1 ? "" : "s"} loaded
        </div>
      )}
    </div>
  );
};
