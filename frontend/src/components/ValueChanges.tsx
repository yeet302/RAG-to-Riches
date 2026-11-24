import type { Evidence, ValueChange } from '../types';

interface Props {
  changes: ValueChange[];
  evidence: Evidence[];
  onSource: (ref: string) => void;
}

export default function ValueChanges({ changes, evidence, onSource }: Props) {
  return (
    <div className="value-changes" role="group" aria-label="Recommended variable and attribute changes">
      <p className="solution-label">What to change</p>
      <ul className="value-change-list" role="list">
        {changes.map((change, index) => (
          <li className="value-change" key={index}>
            <div className="value-change-target"><span>Variable / attribute</span><code>{change.target}</code></div>
            {change.location && evidence.some((source) => source.ref === change.location) ? (
              <button type="button" className="value-change-location" onClick={() => onSource(change.location!)}
                aria-label={`Show edit location for ${change.target}`}><span>Location</span><code>{change.location}</code><span aria-hidden="true">↗</span></button>
            ) : <p className="value-location-unknown">Edit location not confirmed</p>}
            <dl className="value-comparison">
              <div className="value-before"><dt>Value in source</dt><dd>{change.current_value != null
                ? <code>{change.current_value}</code> : <span className="value-unknown">Not confirmed</span>}</dd></div>
              <div className="value-after"><dt><span aria-hidden="true">→ </span>Change to</dt><dd><code>{change.recommended_value}</code></dd></div>
            </dl>
            <details className="value-change-evidence">
              <summary>Why this value?</summary>
              <blockquote>{change.evidence_excerpt}</blockquote>
              <div className="solution-sources">
                {[...new Set(change.evidence_refs)].map((ref) => {
                  const sourceIndex = evidence.findIndex((source) => source.ref === ref);
                  return sourceIndex < 0 ? null : <button type="button" key={ref} onClick={() => onSource(ref)}>
                    Source {sourceIndex + 1} <span aria-hidden="true">↗</span>
                  </button>;
                })}
              </div>
            </details>
          </li>
        ))}
      </ul>
      <p className="value-snapshot-note">Values shown come from retrieved sources; confirm the live setting before editing.</p>
    </div>
  );
}
