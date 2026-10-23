import { useCallback, useMemo, useState } from "react";

import type { ClickUpNode, ConfigRequest, SchedulePreset } from "../types";
import { normalizeKeyword, parseDocId, scheduleToConfig } from "../utils";

export type WizardStep = 1 | 2 | 3 | 4;

/**
 * Holds all wizard form state across the 4 steps and derives the final
 * PUT /config request body. Connection (token + spaces) is owned here; folder
 * lists are fetched per-space by the Select-docs step.
 */
export const useClickUpWizardState = () => {
  const [step, setStep] = useState<WizardStep>(1);

  // Step 1 — Connect
  const [apiToken, setApiToken] = useState("");
  const [workspaceId, setWorkspaceId] = useState("");
  const [spaceIdsInput, setSpaceIdsInput] = useState("");
  const [spaces, setSpaces] = useState<ClickUpNode[]>([]);
  const [isConnected, setIsConnected] = useState(false);

  // Step 2 — Select docs
  const [selectedFolderIds, setSelectedFolderIds] = useState<string[]>([]);
  const [selectAllFolders, setSelectAllFolders] = useState(true);
  const [docIds, setDocIds] = useState<string[]>([]);
  const [excludeKeywords, setExcludeKeywords] = useState<string[]>([]);
  const [deletionEnabled, setDeletionEnabled] = useState(true);
  const [forceReindex, setForceReindex] = useState(false);

  // Step 3 — Schedule
  const [schedulePreset, setSchedulePreset] = useState<SchedulePreset>("6h");
  const [customMinutes, setCustomMinutes] = useState(240);

  const spaceIds = useMemo(
    () =>
      spaceIdsInput
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    [spaceIdsInput],
  );

  const addDocId = useCallback((raw: string) => {
    const id = parseDocId(raw);
    if (!id) return false;
    setDocIds((prev) => (prev.includes(id) ? prev : [...prev, id]));
    return true;
  }, []);

  const removeDocId = useCallback((id: string) => {
    setDocIds((prev) => prev.filter((d) => d !== id));
  }, []);

  const addKeyword = useCallback((raw: string) => {
    const kw = normalizeKeyword(raw);
    if (!kw) return false;
    setExcludeKeywords((prev) => (prev.includes(kw) ? prev : [...prev, kw]));
    return true;
  }, []);

  const removeKeyword = useCallback((kw: string) => {
    setExcludeKeywords((prev) => prev.filter((k) => k !== kw));
  }, []);

  const toggleFolder = useCallback((id: string) => {
    setSelectAllFolders(false);
    setSelectedFolderIds((prev) =>
      prev.includes(id) ? prev.filter((f) => f !== id) : [...prev, id],
    );
  }, []);

  // Build the PUT /config body from the collected selections.
  const buildConfigRequest = useCallback((): ConfigRequest => {
    const schedule = scheduleToConfig(schedulePreset, customMinutes);
    return {
      space_ids: spaceIds,
      // Empty folder list = "all folders" per the API contract.
      folder_ids: selectAllFolders ? [] : selectedFolderIds,
      doc_ids: docIds,
      exclude_name_keywords: excludeKeywords,
      deletion_enabled: deletionEnabled,
      force_reindex: forceReindex,
      enabled: true,
      ...schedule,
    };
  }, [
    schedulePreset,
    customMinutes,
    spaceIds,
    selectAllFolders,
    selectedFolderIds,
    docIds,
    excludeKeywords,
    deletionEnabled,
    forceReindex,
  ]);

  return {
    step,
    setStep,
    // connect
    apiToken,
    setApiToken,
    workspaceId,
    setWorkspaceId,
    spaceIdsInput,
    setSpaceIdsInput,
    spaceIds,
    spaces,
    setSpaces,
    isConnected,
    setIsConnected,
    // select docs
    selectedFolderIds,
    selectAllFolders,
    setSelectAllFolders,
    toggleFolder,
    docIds,
    addDocId,
    removeDocId,
    excludeKeywords,
    addKeyword,
    removeKeyword,
    deletionEnabled,
    setDeletionEnabled,
    forceReindex,
    setForceReindex,
    // schedule
    schedulePreset,
    setSchedulePreset,
    customMinutes,
    setCustomMinutes,
    // derive
    buildConfigRequest,
  };
};

export type ClickUpWizardState = ReturnType<typeof useClickUpWizardState>;
