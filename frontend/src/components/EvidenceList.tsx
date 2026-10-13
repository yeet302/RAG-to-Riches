import { useState } from 'react';
import type { Evidence } from '../types';

const LABEL: Record<Evidence['source_type'], { tag: string; link: string }> = {
  code: { tag: 'Code', link: 'View code' },
  log: { tag: 'Logs', link: 'View logs' },
  metric: { tag: 'Metric', link: 'View traces' },
  doc: { tag: 'Docs', link: 'View doc' },
  incident: { tag: 'Past incident', link: 'View incident' },
};

function EvidenceRow({ e }: { e: Evidence }) {
  const [open, setOpen] = useState(false);
  const l = LABEL[e.source_type];
  return (
    <li className="evidence-row">
      <div className="evidence-main">
        <span className={`tag ${e.source_type}`}>{l.tag}</span>
        <div className="evidence-text">
          <p>{e.summary}</p>
          <code className="ref">{e.ref}</code>
          {e.snippet && (
            <>
              <button className="link small" onClick={() => setOpen(!open)} aria-expanded={open}>
                {open ? 'Hide lines' : 'Show lines'}
              </button>
              {open && <pre className="snippet">{e.snippet}</pre>}
            </>
          )}
        </div>
        {e.link ? (
          <a className="link nowrap" href={e.link} target="_blank" rel="noreferrer">{l.link}</a>
        ) : (
          <span className="muted small nowrap">No link yet</span>
        )}
      </div>
    </li>
  );
}

export default function EvidenceList({ evidence, initial = 3 }: { evidence: Evidence[]; initial?: number }) {
  const [all, setAll] = useState(false);
  const shown = all ? evidence : evidence.slice(0, initial);
  const hidden = evidence.length - shown.length;
  return (
    <>
      <ul className="evidence">
        {shown.map((e, i) => (
          <EvidenceRow key={i} e={e} />
        ))}
      </ul>
      {(hidden > 0 || all) && evidence.length > initial && (
        <button className="link small more" onClick={() => setAll(!all)} aria-expanded={all}>
          {all ? 'Show less' : `Show ${hidden} more piece${hidden === 1 ? '' : 's'} of evidence`}
        </button>
      )}
    </>
  );
}
