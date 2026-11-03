import { useState } from 'react';
import type { CopilotResponse } from '../types';
import { ConfidenceReasons, ConfidenceScore } from './Confidence';
import EvidenceList from './EvidenceList';
import StepList from './StepList';

interface Props {
  response: CopilotResponse;
  onAsk: (q: string) => void;
  conversationId: string;
}

export default function AnswerCard({ response: r, onAsk, conversationId }: Props) {
  const [showWhy, setShowWhy] = useState(false);
  return (
    <>
      <section className="card answer">
        <div className="diag-head">
          <div>
            <p className="eyebrow">Answer</p>
          </div>
          <ConfidenceScore response={r} showWhy={showWhy} onToggle={() => setShowWhy(!showWhy)} />
        </div>
        <ConfidenceReasons response={r} show={showWhy} />
        <p className="answer-text">{r.answer}</p>
        {r.evidence.length > 0 && (
          <>
            <h3 className="sub">Sources</h3>
            <EvidenceList evidence={r.evidence} />
          </>
        )}
        {r.suggestions.length > 0 && (
          <div className="suggestions">
            {r.suggestions.map((s) => (
              <button key={s} className="chip" onClick={() => onAsk(s)}>{s}</button>
            ))}
          </div>
        )}
      </section>
      {r.steps.length > 0 && <StepList steps={r.steps} question={r.question} conversationId={conversationId} />}
    </>
  );
}
