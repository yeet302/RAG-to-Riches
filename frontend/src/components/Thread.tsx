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
                <p className="muted small">Gathering relevant evidence and preparing your answer.</p>
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
            {r.synthesis && (
              <div className="answer-provenance">
                <span className="pill">
                  {r.synthesis.source === 'llm' ? 'AI answer' : r.synthesis.source === 'evidence_fallback' ? 'Evidence fallback' : 'Evidence list'}
                </span>
                {r.synthesis.warning && <p className="muted small" role="status">{r.synthesis.warning}</p>}
              </div>
            )}
            {r.kind === 'diagnosis' ? (
              <DiagnosisCard response={r} conversationId={conversationId} />
            ) : (
              <AnswerCard response={r} onAsk={onAsk} />
            )}
            {r.analysis && (
              <details className="trace">
                <summary>
                  Question analysis: {r.analysis.source === 'llm' ? 'Azure AI' : r.analysis.source === 'keyword_fallback' ? 'Keyword fallback' : 'Keywords'}
                </summary>
                <p>{r.analysis.summary}</p>
                <p className="muted small">{r.analysis.reason}</p>
                {r.analysis.warning && <p role="status">{r.analysis.warning}</p>}
                <ol>
                  {r.analysis.assignments?.slice().sort((a, b) => a.stage - b.stage).map((assignment) => (
                    <li key={assignment.agent}>
                      <strong>{assignment.agent}: Stage {assignment.stage}</strong>
                      <p>{assignment.question}</p>
                      <p className="muted small">{assignment.task}</p>
                    </li>
                  ))}
                </ol>
              </details>
            )}
            <Trace trace={r.trace} agents={r.agents_used} />
          </div>
        );
      })}
    </div>
  );
}
