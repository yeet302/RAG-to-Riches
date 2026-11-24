// Mirrors orchestrator/schemas.py. Keep the two in sync.

export type AgentName = 'logs' | 'docs' | 'code';
export type RoutingMode = 'keyword' | 'llm';
export type AnswerMode = 'evidence' | 'llm';

export interface AnswerSynthesis {
  failure_reason?: 'timeout' | 'access_denied' | 'provider_error' | 'invalid_response' | 'invalid_citations' | 'invalid_solutions' | null;
  source: 'evidence' | 'llm' | 'evidence_fallback';
  citations: string[];
  warning?: string | null;
  context_truncated: boolean;
  latency_ms: number;
}

export interface AgentAssignment {
  agent: AgentName;
  stage: 1 | 2;
  question: string;
  task: string;
}

export interface QueryAnalysis {
  fast_path?: boolean;
  source: 'keyword' | 'llm' | 'keyword_fallback';
  intent: 'incident' | 'how_to' | 'code_lookup' | 'logs_lookup' | 'unknown';
  summary: string;
  reason: string;
  stage1: AgentName[];
  stage2: AgentName[];
  assignments: AgentAssignment[];
  warning?: string | null;
}
export type SourceType = 'log' | 'code' | 'doc' | 'incident' | 'metric';

export interface HealthResponse {
  routing_fast_path?: boolean;
  source_note?: string | null;
  status: string;
  mode: 'local' | 'remote';
  agents: Record<AgentName, string>;
  agent_timeouts_seconds?: Record<AgentName, number>;
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

export interface ValueChange {
  target: string;
  current_value?: string | null;   // retrieved source snapshot, not verified live state
  recommended_value: string;
  location?: string | null;
  evidence_excerpt: string;
  evidence_refs: string[];
}

export interface Step {
  title: string;
  why: string;
  command?: string | null;
  expect?: string | null;
  risk: 'low' | 'medium' | 'high' | 'other_team';
  kind: 'mitigation' | 'fix' | 'verify' | 'escalate';
  link?: string | null;
  evidence_refs: string[];          // sources this step is grounded in
  changes?: ValueChange[];          // optional for stored responses from earlier versions
}

export interface TraceEvent {
  stage: number;
  agent: string;
  action: string;
  parallel: boolean;
  latency_ms: number;
}

export interface PipelineError {
  code: string;
  agent?: AgentName | null;
  stage: 'planning' | 'dispatch' | 'retrieval' | 'model' | 'validation' | 'synthesis';
  message: string;
  resolution: string;
  retryable: boolean;
  http_status?: number | null;
}

export interface PerformanceSummary {
  total_ms: number;
  steps: { name: string; latency_ms: number; parallel: boolean }[];
  slowest_step: string;
  recommendations: string[];
}

export interface CopilotResponse {
  request_id?: string;
  accuracy_recorded?: boolean;
  performance?: PerformanceSummary | null;
  kind: 'diagnosis' | 'answer';
  question: string;
  answer: string;
  likely_cause?: string | null;
  confidence?: number | null;                               // evidence-support score, 0-95
  confidence_band?: 'high' | 'medium' | 'low' | null;
  confidence_reasons: string[];
  confidence_components?: Record<string, number>;
  evidence: Evidence[];
  not_ruled_out?: string | null;
  not_ruled_out_pct?: number | null;
  sources_unavailable: string[];
  steps: Step[];
  agents_used: AgentName[];
  trace: TraceEvent[];
  suggestions: string[];
  pipeline_status: 'ok' | 'partial' | 'failed' | 'no_evidence';
  errors: PipelineError[];
  demo: false;
  analysis?: QueryAnalysis | null;
  synthesis?: AnswerSynthesis | null;
}

export interface IncidentDoc {
  label: string;
  url: string;
}

/** A known incident from orchestrator/incidents_catalog.json, with live status from the logs. */
export interface Incident {
  id: string;
  title: string;
  severity: 'SEV1' | 'SEV2' | 'SEV3';
  status: 'active' | 'recent' | 'not_seen' | 'unknown';
  service: string;
  flag: string;
  tag: string;
  symptom: string;
  question: string;
  occurrences?: number | null;
  last_seen?: string | null;
  documented_in: IncidentDoc[];
}

export interface IncidentFeed {
  incidents: Incident[];
  live: boolean;
  live_detail: string;
  lookback_hours: number;
  checked_at?: string | null;
}

export interface AgentState {
  name: AgentName;
  state: 'ready' | 'degraded' | 'offline' | 'local';
  detail: string;
  latency_ms?: number | null;
  last_run?: { status: string; error_code?: string | null; message?: string | null; at: string } | null;
}

export interface AgentStatusReport {
  agents: AgentState[];
  ready: number;
  total: number;
  checked_at: string;
}

export type Message =
  | { id: string; role: 'user'; text: string; incident?: Incident }
  | { id: string; role: 'assistant'; status: 'loading'; question: string; progress?: InvestigationProgress[] }
  | { id: string; role: 'assistant'; status: 'done'; response: CopilotResponse }
  | { id: string; role: 'assistant'; status: 'error'; error: string; question: string };

export interface AccuracyReview {
  bug: string;
  expected_cause: string;
  correct: 'Not reviewed' | 'Yes' | 'No' | 'Partly';
  fix_worked: 'Not tested' | 'Yes' | 'No';
  notes: string;
}

export interface AccuracyResult extends AccuracyReview {
  request_id: string;
  created_at: string;
  run_type: string;
  response: CopilotResponse;
}

export interface InvestigationProgress {
  phase: 'planning' | 'collecting' | 'agent' | 'synthesis';
  agents?: AgentName[];
  agent?: AgentName;
  status?: 'running' | 'ok' | 'error' | 'no_access' | 'no_evidence';
}
