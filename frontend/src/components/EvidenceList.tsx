import { useEffect, useId, useState } from 'react';
import type { Evidence } from '../types';

const LABEL: Record<Evidence['source_type'], { tag: string; link: string }> = {
  code: { tag: 'Code', link: 'View code' },
  log: { tag: 'Logs', link: 'View logs' },
  metric: { tag: 'Metric', link: 'View traces' },
  doc: { tag: 'Docs', link: 'View doc' },
  incident: { tag: 'Past incident', link: 'View incident' },
};

function EvidenceRow({ e, number, id, selected, selection }: { e: Evidence; number: number; id: string; selected: boolean; selection?: { ref: string } | null }) {
  const [open, setOpen] = useState(false);
  const l = LABEL[e.source_type];
  useEffect(() => { if (selected) setOpen(true); }, [selected, selection]);
  return (
    <li id={id} className={`evidence-row${selected ? ' selected-source' : ''}`} tabIndex={-1}>
      <div className="evidence-main">
        <span className={`tag ${e.source_type}`}>{l.tag}</span>
        <div className="evidence-text">
          <span className="source-number">Source {number}</span>
          <p>{e.summary}</p>
          <code className="ref">{e.ref}</code>
          {e.snippet && (
            <>
              <button className="link small" onClick={() => setOpen(!open)} aria-expanded={open}>
                {open ? 'Hide lines' : 'Show lines'}
              </button>
              {open && <pre className="snippet" tabIndex={0} role="region" aria-label="Source excerpt">{e.snippet}</pre>}
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

export default function EvidenceList({ evidence, initial = 3, focus }: {
  evidence: Evidence[]; initial?: number; focus?: { ref: string } | null;
}) {
  const [all, setAll] = useState(false);
  const listId = useId();
  const selectedIndex = focus ? evidence.findIndex((item) => item.ref === focus.ref) : -1;
  useEffect(() => {
    if (focus && selectedIndex >= initial) setAll(true);
  }, [focus, selectedIndex, initial]);
  useEffect(() => {
    if (!focus || selectedIndex < 0 || (selectedIndex >= initial && !all)) return;
    const frame = requestAnimationFrame(() => {
      const target = document.getElementById(`${listId}-${selectedIndex}`);
      target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      target?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [focus, selectedIndex, initial, all, listId]);
  const shown = all ? evidence : evidence.slice(0, initial);
  const hidden = evidence.length - shown.length;
  return (
    <>
      <ul className="evidence">
        {shown.map((e, i) => (
          <EvidenceRow key={i} e={e} number={i + 1} id={`${listId}-${i}`} selected={i === selectedIndex} selection={focus} />
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
