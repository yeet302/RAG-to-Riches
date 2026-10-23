import type { CopilotResponse } from '../types';
import EvidenceList from './EvidenceList';

interface Props {
  response: CopilotResponse;
  onAsk: (q: string) => void;
}

export default function AnswerCard({ response: r, onAsk }: Props) {
  return (
    <section className="card answer">
      <p className="eyebrow">Answer</p>
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
  );
}
