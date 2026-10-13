import type { Message } from '../types';
import AnswerCard from './AnswerCard';
import DiagnosisCard from './DiagnosisCard';
import Trace from './Trace';

interface Props {
  messages: Message[];
  onAsk: (q: string) => void;
  conversationId: string;
}

export default function Thread({ messages, onAsk, conversationId }: Props) {
  return (
    <div className="thread" aria-live="polite">
      {messages.map((m) => {
        if (m.role === 'user') {
          return (
            <div key={m.id} className="msg user">
              {m.incident && (
                <div className="msg-incident">
                  <span className={`sev ${m.incident.severity.toLowerCase()}`}>{m.incident.severity}</span>
                  <span className="muted small">{m.incident.id} · {m.incident.title}</span>
                </div>
              )}
              <p>{m.text}</p>
            </div>
          );
        }
        if (m.status === 'loading') {
          return (
            <div key={m.id} className="card investigating">
              <span className="spinner" aria-hidden />
              <div>
                <p className="strong">Investigating…</p>
                <p className="muted small">Orchestrator is routing your question to the logs, docs and code agents.</p>
              </div>
            </div>
          );
        }
        if (m.status === 'error') {
          return (
            <div key={m.id} className="card error-card">
              <p className="strong">Copilot couldn't answer that.</p>
              <p className="muted small">{m.error}</p>
              <button className="btn ghost" onClick={() => onAsk(m.question)}>Try again</button>
            </div>
          );
        }
        const r = m.response;
        return (
          <div key={m.id} className="msg assistant">
            {r.kind === 'diagnosis' ? (
              <DiagnosisCard response={r} conversationId={conversationId} />
            ) : (
              <AnswerCard response={r} onAsk={onAsk} />
            )}
            <Trace trace={r.trace} agents={r.agents_used} />
          </div>
        );
      })}
    </div>
  );
}
