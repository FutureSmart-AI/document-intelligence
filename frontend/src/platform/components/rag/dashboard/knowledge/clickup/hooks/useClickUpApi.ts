import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { defaultTo, get } from "lodash-es";

import { useToast } from "@/platform/components/ToastContext";
import { useAuth } from "@/platform/hooks/useAuth";
import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { CLICKUP_API, clickupKeys } from "../constants";
import type {
  ClickUpNode,
  ConfigRequest,
  ConnectorConfig,
  ConnectRequest,
  ConnectResponse,
  DisconnectResponse,
  ResyncResponse,
  SyncResponse,
  SyncRun,
} from "../types";
import { isRunActive } from "../utils";

const errMsg = (error: unknown, fallback: string) =>
  defaultTo(get(error, "response.data.detail"), fallback) as string;

/** GET /config — the saved connector config (404 = never connected). */
export const useClickUpConfig = (enabled = true) => {
  const { knowledgeId } = useAuth();
  return useQuery<ConnectorConfig | null>({
    queryKey: clickupKeys.config(knowledgeId),
    queryFn: async () => {
      try {
        const url = buildApiUrl(CLICKUP_API.CONFIG, {
          knowledge_id: knowledgeId,
        });
        const res = await axios.get<ConnectorConfig>(url);
        return res.data;
      } catch (error) {
        // 404 just means there's no connection yet — surface as null, not an error.
        if (get(error, "response.status") === 404) return null;
        throw error;
      }
    },
    enabled: enabled && !!knowledgeId,
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
};

/** POST /connect — validate token, store it, and return the workspace spaces. */
export const useConnectClickUp = () => {
  const { knowledgeId } = useAuth();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  return useMutation<ConnectResponse, unknown, ConnectRequest>({
    mutationFn: async (body) => {
      const url = buildApiUrl(CLICKUP_API.CONNECT, {
        knowledge_id: knowledgeId,
      });
      const res = await axios.post<ConnectResponse>(url, body);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: clickupKeys.config(knowledgeId),
      });
    },
    onError: (error) => {
      toast.error(errMsg(error, "Invalid ClickUp API token"));
    },
  });
};

/** GET /spaces — list spaces using the stored token. */
export const useClickUpSpaces = (enabled = true) => {
  const { knowledgeId } = useAuth();
  return useQuery<ClickUpNode[]>({
    queryKey: clickupKeys.spaces(knowledgeId),
    queryFn: async () => {
      const url = buildApiUrl(CLICKUP_API.SPACES, {
        knowledge_id: knowledgeId,
      });
      const res = await axios.get<{ spaces: ClickUpNode[] }>(url);
      return defaultTo(get(res, "data.spaces"), []);
    },
    enabled: enabled && !!knowledgeId,
    refetchOnWindowFocus: false,
  });
};

/** GET /folders?space_id= — folder tree for a space. */
export const useClickUpFolders = (spaceId: string | undefined) => {
  const { knowledgeId } = useAuth();
  return useQuery<ClickUpNode[]>({
    queryKey: clickupKeys.folders(knowledgeId, spaceId || ""),
    queryFn: async () => {
      const url = buildApiUrl(CLICKUP_API.FOLDERS, {
        knowledge_id: knowledgeId,
      });
      const res = await axios.get<{ folders: ClickUpNode[] }>(url, {
        params: { space_id: spaceId },
      });
      return defaultTo(get(res, "data.folders"), []);
    },
    enabled: !!knowledgeId && !!spaceId,
    refetchOnWindowFocus: false,
  });
};

/** PUT /config — save selection + schedule (merged server-side). */
export const useSaveClickUpConfig = () => {
  const { knowledgeId } = useAuth();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  return useMutation<ConnectorConfig, unknown, ConfigRequest>({
    mutationFn: async (body) => {
      const url = buildApiUrl(CLICKUP_API.CONFIG, {
        knowledge_id: knowledgeId,
      });
      const res = await axios.put<ConnectorConfig>(url, body);
      return res.data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(clickupKeys.config(knowledgeId), data);
    },
    onError: (error) => {
      toast.error(errMsg(error, "Failed to save ClickUp configuration"));
    },
  });
};

/** POST /sync — queue a sync run. */
export const useTriggerSync = () => {
  const { knowledgeId } = useAuth();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  return useMutation<SyncResponse, unknown, { forceReindex?: boolean }>({
    mutationFn: async (vars) => {
      const url = buildApiUrl(CLICKUP_API.SYNC, {
        knowledge_id: knowledgeId,
      });
      const res = await axios.post<SyncResponse>(url, null, {
        params: { force_reindex: vars.forceReindex ?? false },
      });
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: clickupKeys.runs(knowledgeId),
      });
    },
    onError: (error) => {
      toast.error(errMsg(error, "Failed to start sync"));
    },
  });
};

/** GET /runs — recent sync runs (newest first). Polls while any run is active. */
export const useClickUpRuns = (limit = 20, enabled = true) => {
  const { knowledgeId } = useAuth();
  return useQuery<SyncRun[]>({
    queryKey: clickupKeys.runs(knowledgeId),
    queryFn: async () => {
      const url = buildApiUrl(CLICKUP_API.RUNS, {
        knowledge_id: knowledgeId,
      });
      const res = await axios.get<{ runs: SyncRun[] }>(url, {
        params: { limit },
      });
      return defaultTo(get(res, "data.runs"), []);
    },
    enabled: enabled && !!knowledgeId,
    refetchOnWindowFocus: false,
    // Poll every 4s while the newest run is still queued/running.
    refetchInterval: (query) => {
      const runs = query.state.data as SyncRun[] | undefined;
      return runs && runs.length > 0 && isRunActive(runs[0].status)
        ? 4000
        : false;
    },
  });
};

/** POST /docs/{doc_id}/resync — re-index a single ClickUp doc. */
export const useResyncDoc = () => {
  const { knowledgeId } = useAuth();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  return useMutation<ResyncResponse, unknown, string>({
    mutationFn: async (docId) => {
      const url = buildApiUrl(CLICKUP_API.RESYNC_DOC, {
        knowledge_id: knowledgeId,
        doc_id: docId,
      });
      const res = await axios.post<ResyncResponse>(url);
      return res.data;
    },
    onSuccess: (data) => {
      if (data?.message) toast.success(data.message);
      queryClient.invalidateQueries({
        queryKey: clickupKeys.runs(knowledgeId),
      });
    },
    onError: (error) => {
      toast.error(errMsg(error, "Failed to queue re-sync"));
    },
  });
};

/** DELETE /config?purge_docs= — disconnect, optionally purging indexed docs. */
export const useDisconnectClickUp = () => {
  const { knowledgeId } = useAuth();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  return useMutation<DisconnectResponse, unknown, { purgeDocs: boolean }>({
    mutationFn: async ({ purgeDocs }) => {
      const url = buildApiUrl(CLICKUP_API.CONFIG, {
        knowledge_id: knowledgeId,
      });
      const res = await axios.delete<DisconnectResponse>(url, {
        params: { purge_docs: purgeDocs },
      });
      return res.data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(clickupKeys.config(knowledgeId), null);
      queryClient.invalidateQueries({ queryKey: clickupKeys.all(knowledgeId) });
      if (data?.message) toast.success(data.message);
    },
    onError: (error) => {
      toast.error(errMsg(error, "Failed to disconnect ClickUp"));
    },
  });
};
