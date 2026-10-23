import type { AnswerMode, CopilotResponse, HealthResponse, Incident, RoutingMode } from './types';

// Same origin in production (FastAPI serves the build); Vite proxies /api in dev.
const BASE = import.meta.env.VITE_API_URL ?? '';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthResponse>('/api/health'),
  ask: (question: string, incidentId?: string, conversationId?: string, routingMode?: RoutingMode, answerMode?: AnswerMode) =>
    request<CopilotResponse>('/api/ask', {
      method: 'POST',
      body: JSON.stringify({ question, incident_id: incidentId, conversation_id: conversationId, routing_mode: routingMode, answer_mode: answerMode }),
    }),
  incidents: () => request<Incident[]>('/api/incidents'),
  suggestions: () => request<string[]>('/api/suggestions'),
  feedback: (question: string, stepIndex: number, worked: boolean, conversationId?: string) =>
    request<{ recorded: boolean }>('/api/feedback', {
      method: 'POST',
      body: JSON.stringify({ question, step_index: stepIndex, worked, conversation_id: conversationId }),
    }),
};
