import type { InvestigationProgress, AccuracyResult, AccuracyReview, AnswerMode, CopilotResponse, HealthResponse, Incident, RoutingMode } from './types';

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

async function askStream(question: string, incidentId: string | undefined, conversationId: string,
  onProgress: (event: InvestigationProgress) => void): Promise<CopilotResponse> {
  const res = await fetch(`${BASE}/api/ask/stream`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, incident_id: incidentId, conversation_id: conversationId }),
  });
  if (!res.ok) throw new Error(`Investigation request failed (${res.status}).`);
  if (!res.body) throw new Error('Live updates are unavailable. Please try again.');
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      if (done && buffer.trim()) lines.push(buffer);
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.type === 'progress') onProgress(event);
        if (event.type === 'result') return event.response as CopilotResponse;
        if (event.type === 'error') throw new Error(event.message);
      }
      if (done) throw new Error('Connection ended before the answer arrived. Please try again.');
    }
  } finally {
    await reader.cancel();
    reader.releaseLock();
  }
}

export const api = {
  askStream,
  accuracy: () => request<AccuracyResult[]>('/api/accuracy'),
  accuracyExportUrl: `${BASE}/api/accuracy.csv`,
  reviewAccuracy: (id: string, review: AccuracyReview) => request<AccuracyResult>(`/api/accuracy/${encodeURIComponent(id)}`, {
    method: 'PATCH', body: JSON.stringify(review),
  }),
  health: () => request<HealthResponse>('/api/health'),
  ask: (question: string, incidentId?: string, conversationId?: string, routingMode?: RoutingMode, answerMode?: AnswerMode) =>
    request<CopilotResponse>('/api/ask', {
      method: 'POST',
      body: JSON.stringify({ question, incident_id: incidentId, conversation_id: conversationId, routing_mode: routingMode, answer_mode: answerMode }),
    }),
  incidents: () => request<Incident[]>('/api/incidents'),
  followups: (response: CopilotResponse, recentQuestions: string[]) =>
    request<{ questions: string[]; source: 'llm' | 'unavailable' }>('/api/suggestions', {
      method: 'POST', body: JSON.stringify({
        question: response.question, answer: response.answer.slice(0, 6000),
        sources_unavailable: response.sources_unavailable.slice(0, 10).map((gap) => gap.slice(0, 300)),
        recent_questions: recentQuestions.slice(-3),
      }),
    }),
  suggestions: () => request<string[]>('/api/suggestions'),
  feedback: (question: string, stepIndex: number, worked: boolean, conversationId?: string) =>
    request<{ recorded: boolean }>('/api/feedback', {
      method: 'POST',
      body: JSON.stringify({ question, step_index: stepIndex, worked, conversation_id: conversationId }),
    }),
};
