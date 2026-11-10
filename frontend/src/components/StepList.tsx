import { useState } from 'react';
import { api } from '../api';
import type { Step } from '../types';

const RISK: Record<Step['risk'], string> = {
  low: 'Low risk',
  medium: 'Medium risk',
  high: 'High risk',
  other_team: 'Other team',
};

type Outcome = 'worked' | 'failed' | undefined;

interface Props {
  steps: Step[];
  question: string;
  conversationId: string;
}

export default function StepList({ steps, question, conversationId }: Props) {
  const [active, setActive] = useState(0);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const resolved = outcomes.includes('worked');
  const exhausted = !resolved && steps.length > 0 && steps.every((_, i) => outcomes[i] === 'failed');

  const answer = (i: number, worked: boolean) => {
    api.feedback(question, i, worked, conversationId).catch(() => {});
    setOutcomes((o) => {
      const n = [...o];
      n[i] = worked ? 'worked' : 'failed';
      return n;
    });
    if (!worked) setActive(Math.min(i + 1, steps.length - 1));
  };

  return (
    <section className="card steps-card" aria-label="What to try">
      <h2 className="steps-title">What to try</h2>
      <p className="muted">
        Suggested from the sources above. Use Previous and Next to review steps; record whether a step worked separately.
      </p>
      <ol className="steps">
        {steps.map((s, i) => {
          const isActive = i === active;
          const outcome = outcomes[i];
          return (
            <li key={i} className={`step${isActive ? ' active' : ''}${outcome ? ` ${outcome}` : ''}`}>
              <div className="step-head">
                <span className="num">{outcome === 'worked' ? '✓' : i + 1}</span>
                <span className="step-title">{s.title}</span>
                <span className="spacer" />
                <span className={`risk ${s.risk}`}>{RISK[s.risk]}</span>
              </div>
              {isActive && (
                <div className="step-body">
                  <p className="why"><b>Why:</b> {s.why}</p>
                  {s.evidence_refs?.length > 0 && (
                    <p className="based-on muted small">
                      Based on: {s.evidence_refs.map((ref) => <code key={ref}>{ref}</code>)}
                    </p>
                  )}
                  {s.command && <pre className="command">{s.command}</pre>}
                  {s.expect && <p><b>Expect:</b> {s.expect}</p>}
                  <div className="did-it-work">
                    <span className="strong">Did this work?</span>
                    <button className="btn ghost" onClick={() => answer(i, true)}>Yes</button>
                    <button className="btn ghost" onClick={() => answer(i, false)}>
                      {i < steps.length - 1 ? 'No, next step' : 'No'}
                    </button>
                  </div>
                </div>
              )}
              {outcome === 'failed' && <p className="muted small step-note">Previously marked as not working.</p>}
            </li>
          );
        })}
      </ol>
      <nav className="step-navigation" aria-label="Suggested step navigation">
        <button type="button" className="btn ghost" disabled={active === 0}
          onClick={() => setActive((index) => Math.max(0, index - 1))}>Previous step</button>
        <span className="muted small" aria-live="polite">Step {active + 1} of {steps.length}</span>
        <button type="button" className="btn ghost" disabled={active >= steps.length - 1}
          onClick={() => setActive((index) => Math.min(steps.length - 1, index + 1))}>Next step</button>
      </nav>
      {resolved && (
        <p className="resolved-note">A step was marked as worked. You can review earlier steps without changing their recorded outcomes.</p>
      )}
      {exhausted && (
        <p className="resolved-note warn">
          All steps were marked as not working. Review a previous step or investigate further using the evidence above.
        </p>
      )}
    </section>
  );
}
