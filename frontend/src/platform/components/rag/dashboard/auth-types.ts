import type { IAgent } from "@/platform/types/agent";
import type { UserRole } from "@/platform/types/roles";

export type Profile = {
  session_id: string;
  user_id: string;
  email: string;
  name: string;
  picture: string;
  user_tier: string;
  service_count: number;
  energy: number;
  last_accessed_date: string;
  last_accessed_time: string;
  /** Absolute session expiry — ISO-8601 UTC without a zone designator (e.g. "2026-07-05T20:28:28"). */
  expires_at?: string;
  issued_at?: string;
  is_authenticated?: boolean;
  time_remaining?: string;
  refresh_date?: string;
  refresh_time?: string;
};

export type IAuthContext = {
  profile: Profile | null;
  selectedAgent: IAgent | null;
  agentId: string | null;
  knowledgeId: string | null;
  isKnowledgeEnabled: boolean;
  refetchSelectedAgent: () => void;
  isNl2sqlEnabled: boolean;
  nl2sqlId: string | null;
  actionsServiceIds: string[];
  integrationServiceIds: string[];
  userRole: UserRole | null;
  isOwner: boolean;
  isEditor: boolean;
  isViewer: boolean;
  /** Absolute epoch-ms when the session expires; null when unknown. */
  sessionExpiresAt: number | null;
  /** Renew the session via /refresh, then refetch the profile. Rejects-safe:
   * on failure it redirects to login. Resolves true when the expiry actually
   * moved past the warning window, false when the server accepted the refresh
   * without extending the session (or the refresh failed). */
  stayLoggedIn: () => Promise<boolean>;
};
