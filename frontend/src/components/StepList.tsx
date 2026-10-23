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
  const exhausted = !resolved && active >= steps.length;

  const answer = (i: number, worked: boolean) => {
    api.feedback(question, i, worked, conversationId).catch(() => {});
    setOutcomes((o) => {
      const n = [...o];
      n[i] = worked ? 'worked' : 'failed';
      return n;
    });
    if (!worked) setActive(i + 1);
  };

  return (
    <section className="card steps-card">
      <h2 className="steps-title">What to try</h2>
      <p className="muted">One step at a time. Tell Copilot if it worked and it moves to the next step.</p>
      <ol className="steps">
        {steps.map((s, i) => {
          const isActive = i === active && !resolved;
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
              {outcome === 'failed' && <p className="muted small step-note">Didn't work. Moved to the next step.</p>}
            </li>
          );
        })}
      </ol>
      {resolved && (
        <p className="resolved-note">Marked as fixed. Copilot will suggest this step first for similar incidents.</p>
      )}
      {exhausted && (
        <p className="resolved-note warn">
          None of the steps worked. Copilot will gather more evidence; consider escalating with the evidence above.
        </p>
      )}
    </section>
  );
}
