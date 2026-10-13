import { useState } from 'react';
import type { CopilotResponse } from '../types';
import EvidenceList from './EvidenceList';
import StepList from './StepList';

interface Props {
  response: CopilotResponse;
  conversationId: string;
}

export default function DiagnosisCard({ response: r, conversationId }: Props) {
  const [showWhy, setShowWhy] = useState(false);
  return (
    <>
      <section className="card diagnosis">
        <div className="diag-head">
          <div>
            <p className="eyebrow">Likely cause</p>
            <h2 className="cause">{r.likely_cause}</h2>
          </div>
          {r.confidence != null && (
            <div className="confidence">
              <span className="pct">{r.confidence}%</span>
              <span className="muted small">confidence</span>
              <button className="link small" onClick={() => setShowWhy(!showWhy)} aria-expanded={showWhy}>
                {showWhy ? 'Hide' : `Why ${r.confidence}%?`}
              </button>
            </div>
          )}
        </div>

        {showWhy && r.confidence_reasons.length > 0 && (
          <ul className="reasons">
            {r.confidence_reasons.map((x) => <li key={x}>{x}</li>)}
          </ul>
        )}

        <h3 className="sub">Why Copilot thinks so</h3>
        <EvidenceList evidence={r.evidence} />

        {r.not_ruled_out && (
          <p className="not-ruled-out">
            <b>Not ruled out ({r.not_ruled_out_pct}%):</b> {r.not_ruled_out}
          </p>
        )}
        {r.sources_unavailable.length > 0 && (
          <p className="muted small">Copilot couldn't see: {r.sources_unavailable.join(', ')}.</p>
        )}
      </section>

      {r.steps.length > 0 && <StepList steps={r.steps} question={r.question} conversationId={conversationId} />}
    </>
  );
}
