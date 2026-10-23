import { useCallback } from "react";

import { ArrowLeft, ArrowRight } from "lucide-react";

import { useToast } from "@/platform/components/ToastContext";
import { Button } from "@/platform/components/ui";

import { StepProgress } from "./components/StepProgress";
import {
  useConnectClickUp,
  useSaveClickUpConfig,
  useTriggerSync,
} from "./hooks/useClickUpApi";
import { useClickUpWizardState } from "./hooks/useClickUpWizardState";
import { ConnectStep } from "./steps/ConnectStep";
import { ReviewStep } from "./steps/ReviewStep";
import { ScheduleStep } from "./steps/ScheduleStep";
import { SelectDocsStep } from "./steps/SelectDocsStep";

interface ClickUpWizardProps {
  /** Called after indexing has been queued — refetch + close the dialog. */
  onSuccess: () => void;
  /** Called from "Back" on step 1 — return to the type selector. */
  onCancel: () => void;
}

/** The 4-step ClickUp connect → select → schedule → review wizard. */
export const ClickUpWizard = ({ onSuccess, onCancel }: ClickUpWizardProps) => {
  const { toast } = useToast();
  const wizard = useClickUpWizardState();
  const connect = useConnectClickUp();
  const saveConfig = useSaveClickUpConfig();
  const triggerSync = useTriggerSync();

  // Space ID is optional — an empty space_ids list indexes the whole workspace.
  const connectFilled = !!wizard.apiToken.trim() && !!wizard.workspaceId.trim();

  const handleConnect = useCallback(async () => {
    try {
      const res = await connect.mutateAsync({
        api_token: wizard.apiToken.trim(),
        workspace_id: wizard.workspaceId.trim(),
      });
      wizard.setSpaces(res.spaces);
      wizard.setIsConnected(true);
      wizard.setStep(2);
    } catch {
      // Error is surfaced via the mutation's onError toast; stay on this step.
    }
  }, [connect, wizard]);

  const handleStartIndexing = useCallback(async () => {
    try {
      await saveConfig.mutateAsync(wizard.buildConfigRequest());
      await triggerSync.mutateAsync({ forceReindex: wizard.forceReindex });
      toast.success(
        "Indexing started — monitor progress in the knowledge list.",
      );
      onSuccess();
    } catch {
      // Save / sync errors are surfaced via mutation onError toasts.
    }
  }, [saveConfig, triggerSync, wizard, toast, onSuccess]);

  const handleNext = useCallback(() => {
    if (wizard.step === 1) {
      handleConnect();
    } else if (wizard.step === 4) {
      handleStartIndexing();
    } else {
      wizard.setStep((wizard.step + 1) as typeof wizard.step);
    }
  }, [wizard, handleConnect, handleStartIndexing]);

  const handleBack = useCallback(() => {
    if (wizard.step === 1) {
      onCancel();
    } else {
      wizard.setStep((wizard.step - 1) as typeof wizard.step);
    }
  }, [wizard, onCancel]);

  const isLastStep = wizard.step === 4;
  const nextDisabled = wizard.step === 1 && !connectFilled;
  const nextLoading =
    connect.isPending || saveConfig.isPending || triggerSync.isPending;

  return (
    <div className="space-y-1">
      <StepProgress current={wizard.step} />

      {wizard.step === 1 && <ConnectStep wizard={wizard} />}
      {wizard.step === 2 && <SelectDocsStep wizard={wizard} />}
      {wizard.step === 3 && <ScheduleStep wizard={wizard} />}
      {wizard.step === 4 && <ReviewStep wizard={wizard} />}

      <div className="flex justify-between gap-2 pt-4">
        <Button
          variant="ghost"
          onClick={handleBack}
          disabled={nextLoading}
          startIcon={<ArrowLeft className="size-4" />}
        >
          Back
        </Button>
        <Button
          variant="primary"
          onClick={handleNext}
          loading={nextLoading}
          loadingText={isLastStep ? "Starting…" : "Verifying…"}
          disabled={nextDisabled || nextLoading}
          endIcon={!isLastStep ? <ArrowRight className="size-4" /> : undefined}
        >
          {isLastStep
            ? "Start indexing"
            : wizard.step === 1
              ? "Connect"
              : "Next"}
        </Button>
      </div>
    </div>
  );
};
