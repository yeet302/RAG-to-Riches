import { useState } from 'react';
import type { CopilotResponse } from '../types';
import { ConfidenceReasons, ConfidenceScore } from './Confidence';
import EvidenceList from './EvidenceList';
import StepList from './StepList';
import CitedAnswer from './CitedAnswer';

interface Props {
  response: CopilotResponse;
  conversationId: string;
}

export default function DiagnosisCard({ response: r, conversationId }: Props) {
  const [showWhy, setShowWhy] = useState(false);
  const [selectedSource, setSelectedSource] = useState<{ ref: string } | null>(null);
  return (
    <>
      <section className="card diagnosis">
        <div className="diag-head">
          <div>
            <p className="eyebrow">{r.likely_cause ? 'Likely cause' : 'Investigation results'}</p>
            <h2 className="cause">{r.likely_cause ?? 'Relevant evidence'}</h2>
          </div>
          <ConfidenceScore response={r} showWhy={showWhy} onToggle={() => setShowWhy(!showWhy)} />
        </div>

        <ConfidenceReasons response={r} show={showWhy} />

        <CitedAnswer text={r.answer} evidence={r.evidence} onSource={(ref) => setSelectedSource({ ref })} />

        <h3 className="sub">{r.likely_cause ? 'Why Copilot thinks so' : 'Sources'}</h3>
        <EvidenceList evidence={r.evidence} focus={selectedSource} />

        {r.not_ruled_out && (
          <p className="not-ruled-out">
            <b>Not ruled out{r.not_ruled_out_pct != null ? ` (${r.not_ruled_out_pct}%)` : ''}:</b> {r.not_ruled_out}
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
