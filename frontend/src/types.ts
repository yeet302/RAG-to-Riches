// Mirrors orchestrator/schemas.py. Keep the two in sync.

export type AgentName = 'logs' | 'docs' | 'code';
export type RoutingMode = 'keyword' | 'llm';
export type AnswerMode = 'evidence' | 'llm';

export interface AnswerSynthesis {
  source: 'evidence' | 'llm' | 'evidence_fallback' | 'demo';
  citations: string[];
  warning?: string | null;
  context_truncated: boolean;
  latency_ms: number;
}

export interface QueryAnalysis {
  source: 'keyword' | 'llm' | 'keyword_fallback';
  intent: 'incident' | 'how_to' | 'code_lookup' | 'logs_lookup' | 'unknown';
  summary: string;
  reason: string;
  stage1: AgentName[];
  stage2: AgentName[];
  warning?: string | null;
}
export type SourceType = 'log' | 'code' | 'doc' | 'incident' | 'metric';

export interface HealthResponse {
  status: string;
  mode: 'local' | 'remote' | 'demo';
  agents: Record<AgentName, string>;
  routing_mode: RoutingMode;
  llm_configured: boolean;
  answer_mode: AnswerMode;
}

export interface Evidence {
  source_type: SourceType;
  summary: string;
  ref: string;
  link?: string | null;
  snippet?: string | null;
  agent?: AgentName | null;
}

export interface Step {
  title: string;
  why: string;
  command?: string | null;
  expect?: string | null;
  risk: 'low' | 'medium' | 'high' | 'other_team';
  kind: 'mitigation' | 'fix' | 'verify' | 'escalate';
  link?: string | null;
}

export interface TraceEvent {
  stage: number;
  agent: string;
  action: string;
  parallel: boolean;
  latency_ms: number;
}

export interface CopilotResponse {
  kind: 'diagnosis' | 'answer';
  question: string;
  answer: string;
  likely_cause?: string | null;
  confidence?: number | null;
  confidence_reasons: string[];
  evidence: Evidence[];
  not_ruled_out?: string | null;
  not_ruled_out_pct?: number | null;
  sources_unavailable: string[];
  steps: Step[];
  agents_used: AgentName[];
  trace: TraceEvent[];
  suggestions: string[];
  demo: boolean;
  analysis?: QueryAnalysis | null;
  synthesis?: AnswerSynthesis | null;
}

export interface Incident {
  id: string;
  title: string;
  severity: 'SEV1' | 'SEV2' | 'SEV3';
  status: 'Open' | 'Investigating' | 'Resolved';
  service: string;
  team: string;
  started: string;
  assignee?: string | null;
  summary_label: string;
  summary: string;
  other_team: boolean;
  question: string;
}

export type Message =
  | { id: string; role: 'user'; text: string; incident?: Incident }
  | { id: string; role: 'assistant'; status: 'loading'; question: string }
  | { id: string; role: 'assistant'; status: 'done'; response: CopilotResponse }
  | { id: string; role: 'assistant'; status: 'error'; error: string; question: string };
