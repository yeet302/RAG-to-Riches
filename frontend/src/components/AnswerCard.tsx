import { useState } from 'react';
import type { CopilotResponse } from '../types';
import { ConfidenceReasons, ConfidenceScore } from './Confidence';
import EvidenceList from './EvidenceList';
import StepList from './StepList';
import CitedAnswer from './CitedAnswer';

interface Props {
  response: CopilotResponse;
  onAsk: (q: string) => void;
  conversationId: string;
}

export default function AnswerCard({ response: r, onAsk, conversationId }: Props) {
  const [showWhy, setShowWhy] = useState(false);
  const [selectedSource, setSelectedSource] = useState<{ ref: string } | null>(null);
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
        <CitedAnswer text={r.answer} evidence={r.evidence} onSource={(ref) => setSelectedSource({ ref })} />
        {r.evidence.length > 0 && (
          <>
            <h3 className="sub">Sources</h3>
            <EvidenceList evidence={r.evidence} focus={selectedSource} />
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
