/**
 * The agent record the shared chat components were written against.
 *
 * A Document Intelligence collection has no agent behind it — `AuthBridge`
 * always supplies `selectedAgent: null` — so nothing here is ever fetched. The
 * type stays only because the shared components read optional agent settings
 * off it (`selectedAgent?.config?.agent_params`) and fall back when it is absent.
 */
export interface IService {
  service_type: string;
  service_id: string;
  status: string;
  created_at: string;
  updated_at: string;
}

interface IDefaultResponseNode {
  model: string;
  provider: string;
  max_tokens: number;
  temperature: number;
}

export interface IAgentParams {
  agent_name: string;
  show_sources: boolean;
  follow_up: boolean;
  suggested_questions: string[];
  default_response_node: IDefaultResponseNode;
  chart_output: boolean;
  memory_enabled: boolean;
  global_memory: boolean;
  voice_input?: boolean;
  voice_output?: boolean;
  raw_response?: boolean;
  default_language?: string | null;
  is_public?: boolean;
  allowed_domains?: string[];
}

export interface IAgentConfig {
  agent_params: IAgentParams;
}

export interface IAgent {
  agent_id: string;
  owner_id: string;
  services?: IService[];
  config: IAgentConfig;
  created_at: string;
  updated_at: string;
}
export interface IAgents {
  data: IAgent[];
  next_cursor: boolean | null;
}
