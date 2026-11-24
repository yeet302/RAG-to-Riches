import type { Incident, IncidentFeed } from '../types';

interface Props {
  feed: IncidentFeed | null;
  error: string | null;
  onOpen: (incident: Incident) => void;
}

const STATUS_LABEL: Record<Incident['status'], string> = {
  active: 'Active now',
  recent: 'Seen recently',
  not_seen: 'Not seen',
  unknown: 'Status unknown',
};

function ago(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

function activity(inc: Incident, lookbackHours: number): string {
  const window = lookbackHours % 24 === 0 && lookbackHours >= 48 ? `${lookbackHours / 24} days` : `${lookbackHours} h`;
  if (inc.last_seen) {
    return `${inc.occurrences ?? 0} log events in the last ${window} · last seen ${ago(inc.last_seen)} ` +
      `(${new Date(inc.last_seen).toLocaleString()})`;
  }
  if (inc.status === 'not_seen') return `No log events in the last ${window}`;
  return 'Live status unavailable';
}

export default function IncidentList({ feed, error, onOpen }: Props) {
  const incidents = feed?.incidents ?? [];
  return (
    <section aria-labelledby="incidents-heading">
      <div className="incidents-head">
        <h2 id="incidents-heading" className="section-title">Your team's incidents</h2>
        {feed && <p className={`muted small live-note${feed.live ? ' live' : ''}`}>{feed.live_detail}</p>}
      </div>
      {error && <p className="error-text">Couldn't load incidents: {error}. Is the orchestrator running on port 8000?</p>}
      {!error && feed && incidents.length === 0 && (
        <p className="muted">No known incidents are configured. Ask a question above to investigate.</p>
      )}
      {!error && !feed && <p className="muted">Loading incidents…</p>}
      <div className="incident-list">
        {incidents.map((inc) => (
          <article key={inc.id} className={`card incident status-${inc.status}`}>
            <div className="incident-meta">
              <span className={`sev ${inc.severity.toLowerCase()}`}>{inc.severity}</span>
              <span className={`incident-status ${inc.status}`}>
                {inc.status === 'active' && <span className="status-dot bad" aria-hidden />}
                {STATUS_LABEL[inc.status]}
              </span>
              <span className="spacer" />
              <span className="muted small">{inc.id} · flag <code>{inc.flag}</code></span>
            </div>
            <h3 className="incident-title">
              <button className="title-link" onClick={() => onOpen(inc)}>{inc.title}</button>
            </h3>
            <p className="incident-summary">{inc.symptom}</p>
            <p className="muted small incident-activity">
              {activity(inc, feed?.lookback_hours ?? 168)} · log tag <code>{inc.tag}</code>
            </p>
            <div className="incident-foot">
              <button className={`btn ${inc.status === 'active' ? 'dark' : 'ghost'}`} onClick={() => onOpen(inc)}>
                Investigate
              </button>
              {inc.documented_in.length > 0 && (
                <span className="muted small incident-docs">
                  Documented in:{' '}
                  {inc.documented_in.map((doc, i) => (
                    <span key={doc.url}>
                      {i > 0 && ' · '}
                      <a href={doc.url} target="_blank" rel="noreferrer">{doc.label}</a>
                    </span>
                  ))}
                </span>
              )}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
