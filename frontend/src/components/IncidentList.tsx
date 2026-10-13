import type { Incident } from '../types';

interface Props {
  incidents: Incident[];
  error: string | null;
  onOpen: (incident: Incident) => void;
}

export default function IncidentList({ incidents, error, onOpen }: Props) {
  return (
    <section aria-labelledby="incidents-heading">
      <h2 id="incidents-heading" className="section-title">Your team's incidents</h2>
      {error && <p className="error-text">Couldn't load incidents: {error}. Is the orchestrator running on port 8000?</p>}
      {!error && incidents.length === 0 && <p className="muted">Loading incidents…</p>}
      <div className="incident-list">
        {incidents.map((inc, i) => (
          <article key={inc.id} className={`card incident${i === 0 ? ' focus' : ''}`}>
            <div className="incident-meta">
              <span className={`sev ${inc.severity.toLowerCase()}`}>{inc.severity}</span>
              <span className={`status ${inc.status.toLowerCase()}`}>{inc.status}</span>
              <span className="spacer" />
              <span className="muted small">
                {inc.started} · {inc.assignee ? (inc.assignee === 'you' ? 'Assigned to you' : inc.assignee) : 'Unassigned'}
              </span>
            </div>
            <h3 className="incident-title">
              <button className="title-link" onClick={() => onOpen(inc)}>{inc.title}</button>
            </h3>
            <p className="incident-summary">
              <b>{inc.summary_label}:</b> {inc.summary}{' '}
              {inc.other_team && <span className="other-team">Not your team's code.</span>}
            </p>
            {inc.status !== 'Resolved' && (
              <button className={`btn ${i === 0 ? 'dark' : 'ghost'}`} onClick={() => onOpen(inc)}>
                See diagnosis
              </button>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
