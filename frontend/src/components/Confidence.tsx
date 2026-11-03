import type { CopilotResponse } from '../types';

const BAND_LABEL = { high: 'High support', medium: 'Medium support', low: 'Low support' } as const;

interface ScoreProps {
  response: CopilotResponse;
  showWhy: boolean;
  onToggle: () => void;
}

/** The score itself: percent, band, and the "Why N%?" toggle. */
export function ConfidenceScore({ response: r, showWhy, onToggle }: ScoreProps) {
  if (r.confidence == null) return null;
  const band = r.confidence_band ?? 'low';
  return (
    <div className={`confidence band-${band}`}>
      <span className="pct">{r.confidence}%</span>
      <span className={`band-pill ${band}`}>{BAND_LABEL[band]}</span>
      {r.confidence_reasons.length > 0 && (
        <button className="link small" onClick={onToggle} aria-expanded={showWhy}>
          {showWhy ? 'Hide' : `Why ${r.confidence}%?`}
        </button>
      )}
    </div>
  );
}

/** The explanation shown under the answer when "Why N%?" is open. */
export function ConfidenceReasons({ response: r, show }: { response: CopilotResponse; show: boolean }) {
  if (!show || r.confidence == null || r.confidence_reasons.length === 0) return null;
  return (
    <div className="reasons">
      <p className="muted small">
        How strongly the evidence Copilot collected supports this answer. It is not a guarantee that the
        answer is right.
      </p>
      <ul>
        {r.confidence_reasons.map((x) => <li key={x}>{x}</li>)}
      </ul>
    </div>
  );
}
