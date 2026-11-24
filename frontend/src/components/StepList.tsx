import { useId, useState } from 'react';
import { api } from '../api';
import type { Evidence, Step } from '../types';

const RISK: Record<Step['risk'], string> = {
  low: 'Low risk',
  medium: 'Medium risk',
  high: 'High risk',
  other_team: 'Other team',
};

const GROUP: Record<Step['kind'], { title: string; label: string; description: string }> = {
  mitigation: { title: 'Stabilize the service', label: 'Mitigation', description: 'Reduce the immediate impact.' },
  fix: { title: 'Apply the fix', label: 'Fix', description: 'Make the changes supported by the evidence.' },
  verify: { title: 'Check the result', label: 'Verification', description: 'Test the hypothesis or confirm the change worked.' },
  escalate: { title: 'Bring in the right team', label: 'Escalation', description: 'Follow up with the team that owns this component.' },
};

type Outcome = 'worked' | 'failed' | undefined;

interface Props {
  steps: Step[];
  question: string;
  conversationId: string;
  evidence: Evidence[];
  onSource: (ref: string) => void;
}

export default function StepList({ steps, question, conversationId, evidence, onSource }: Props) {
  const planId = useId();
  const [active, setActive] = useState(0);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [copied, setCopied] = useState<number | null>(null);
  const [copyError, setCopyError] = useState<number | null>(null);
  const [feedbackError, setFeedbackError] = useState(false);
  const reviewed = outcomes.filter(Boolean).length;
  const resolved = outcomes.includes('worked');
  const exhausted = !resolved && steps.every((_, i) => outcomes[i] === 'failed');

  // Keep the recommended sequence and original feedback indices, including when a kind recurs.
  const groups: { kind: Step['kind']; actions: { step: Step; index: number }[] }[] = [];
  steps.forEach((step, index) => {
    if (groups[groups.length - 1]?.kind !== step.kind) groups.push({ kind: step.kind, actions: [] });
    groups[groups.length - 1].actions.push({ step, index });
  });

  const select = (index: number) => {
    setActive(index);
    const target = document.getElementById(`${planId}-action-${index}`);
    target?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' });
    target?.focus({ preventScroll: true });
  };

  const answer = (index: number, worked: boolean) => {
    api.feedback(question, index, worked, conversationId).catch(() => setFeedbackError(true));
    setOutcomes((previous) => {
      const next = [...previous];
      next[index] = worked ? 'worked' : 'failed';
      return next;
    });
    if (!worked) setActive(Math.min(index + 1, steps.length - 1));
  };

  const copy = async (index: number, command: string) => {
    setCopied(null);
    setCopyError(null);
    try {
      await navigator.clipboard.writeText(command);
      setCopied(index);
    } catch {
      setCopyError(index);
    }
  };

  return (
    <section className="card solutions-plan" aria-labelledby={`${planId}-title`}>
      <header className="solutions-heading">
        <span className="solutions-icon" aria-hidden="true">↗</span>
        <div>
          <p className="eyebrow">Your action plan</p>
          <h2 id={`${planId}-title`}>Suggested fixes</h2>
        </div>
        <span className="solutions-count">{steps.length} {steps.length === 1 ? 'action' : 'actions'}</span>
      </header>
      <p className="solutions-intro">Follow the numbered actions in order. Review each change and its risk before applying it.</p>
      <div className="solutions-toolbar">
        <nav className="solutions-jumps" aria-label="Jump to an action">
          {steps.map((step, index) => (
            <button type="button" key={index} title={step.title} aria-label={`Go to action ${index + 1}: ${step.title}`}
              aria-current={index === active ? 'step' : undefined} onClick={() => select(index)}>
              <span>#{index + 1}</span> {GROUP[step.kind].label}
              {outcomes[index] && <span className="sr-only">{outcomes[index] === 'worked' ? 'Worked' : "Didn't work"}</span>}
            </button>
          ))}
        </nav>
        <span className="solutions-reviewed" role="status">{reviewed} of {steps.length} reviewed</span>
      </div>

      <div className="solution-groups">
        {groups.map((group) => (
          <section className={`solution-group ${group.kind}`} key={group.actions[0].index}
            aria-labelledby={`${planId}-group-${group.actions[0].index}`}>
            <div className="solution-group-heading">
              <h3 id={`${planId}-group-${group.actions[0].index}`}>{GROUP[group.kind].title}</h3>
              <p>{GROUP[group.kind].description}</p>
            </div>
            <ol className="solution-actions" role="list" start={group.actions[0].index + 1}>
              {group.actions.map(({ step, index }) => {
                const outcome = outcomes[index];
                const sources = evidence.filter((item, sourceIndex) => step.evidence_refs.includes(item.ref)
                  && evidence.findIndex((source) => source.ref === item.ref) === sourceIndex);
                return (
                  <li id={`${planId}-action-${index}`} key={index} tabIndex={-1} value={index + 1}
                    className={`solution-action${index === active ? ' active' : ''}${outcome ? ` ${outcome}` : ''}`}>
                    <div className="solution-action-heading">
                      <span className="solution-number" aria-hidden="true">#{index + 1}</span>
                      <div className="solution-action-title">
                        <p className="solution-state">{outcome === 'worked' ? '✓ Marked as worked' : outcome === 'failed' ? "Marked as didn't work" : index === active ? (index === 0 ? 'Start here' : 'Current action') : GROUP[step.kind].label}</p>
                        <h4>{step.title}</h4>
                      </div>
                      <span className={`risk ${step.risk}`}>{RISK[step.risk]}</span>
                    </div>
                    <div className="solution-action-body">
                      <div className="solution-reason"><span className="solution-label">Why this helps</span><p>{step.why}</p></div>
                      {step.command && (
                        <div className="solution-change">
                          <div className="solution-change-heading">
                            <span className="solution-label">Command / change</span>
                            <button type="button" className="solution-copy" onClick={() => void copy(index, step.command!)}
                              aria-label={`Copy command for action ${index + 1}`}>{copied === index ? '✓ Copied' : 'Copy command'}</button>
                          </div>
                          <pre className="command" tabIndex={0} aria-label={`Command for action ${index + 1}`}><code>{step.command}</code></pre>
                          <span className="sr-only" role="status">{copied === index ? `Command for action ${index + 1} copied.` : ''}</span>
                          {copyError === index && <p className="small" role="status">Copy unavailable. Select the command above and copy it manually.</p>}
                        </div>
                      )}
                      {step.expect && <div className="solution-expect"><span aria-hidden="true">✓</span><div><span className="solution-label">What to look for</span><p>{step.expect}</p></div></div>}
                      {sources.length > 0 && (
                        <div className="solution-sources"><span>Supporting sources</span>
                          {sources.map((source) => <button type="button" key={source.ref} title={source.summary}
                            onClick={() => onSource(source.ref)}>Source {evidence.indexOf(source) + 1} <span aria-hidden="true">↗</span></button>)}
                        </div>
                      )}
                      <div className="solution-feedback">
                        <span>Did this action work?</span>
                        <div>
                          <button type="button" aria-pressed={outcome === 'worked'} className="solution-worked" onClick={() => answer(index, true)}>✓ Worked</button>
                          <button type="button" aria-pressed={outcome === 'failed'} onClick={() => answer(index, false)}>Didn't work</button>
                        </div>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ol>
          </section>
        ))}
      </div>
      {resolved && <p className="resolved-note" role="status">You marked an action as worked. Confirm the original issue is resolved{steps.some((step) => step.kind === 'verify') ? ' using the verification actions' : ''}.</p>}
      {exhausted && <p className="resolved-note warn" role="status">None of these actions worked. Use the supporting evidence below to investigate further or involve the owning team.</p>}
      {feedbackError && <p className="muted small" role="status">Your review is shown on this page, but feedback could not be saved to the server.</p>}
      <p className="solutions-footnote">Suggested from the investigation evidence. Commands are shown only when supported by a source.</p>
    </section>
  );
}
