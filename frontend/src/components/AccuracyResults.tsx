import { useEffect, useState } from 'react';
import { api } from '../api';
import type { AccuracyResult, AccuracyReview } from '../types';
import Performance from './Performance';

export default function AccuracyResults() {
  const [rows, setRows] = useState<AccuracyResult[]>([]);
  const [selected, setSelected] = useState<AccuracyResult | null>(null);
  const [review, setReview] = useState<AccuracyReview | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const load = () => {
    setLoading(true); setError('');
    api.accuracy().then(setRows).catch((e) => setError(e.message)).finally(() => setLoading(false));
  };
  useEffect(load, []);
  const choose = (row: AccuracyResult) => {
    setSelected(row); setNotice('');
    setReview({ bug: row.bug, expected_cause: row.expected_cause, correct: row.correct, fix_worked: row.fix_worked, notes: row.notes });
  };
  const save = async () => {
    if (!selected || !review) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const updated = await api.reviewAccuracy(selected.request_id, review);
      setRows((current) => current.map((row) => row.request_id === updated.request_id ? updated : row));
      setSelected(updated); setNotice('Review saved. The CSV export includes your changes.');
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return <section className="accuracy-workbench">
    <div className="workbench-heading"><div><h1>Accuracy results</h1><p className="muted">Every investigation is saved. Review the answer, verify the fix, then export.</p></div>
      <a className="btn dark export-link" href={api.accuracyExportUrl}>Download CSV</a>
    </div>
    <p className="source-notice">Local results log. Export these rows to the shared accuracy sheet when it is ready. Historical command-line runs remain in results/accuracy_results.csv.</p>
    <div className="workbench-summary"><strong>{rows.length} recent runs</strong><span>{rows.filter((row) => row.correct === 'Not reviewed').length} awaiting review</span><button className="btn ghost" onClick={load} disabled={loading}>Refresh</button></div>
    {error && <p className="error-text" role="alert">{error}</p>}
    {loading && <p role="status">Loading recorded investigations...</p>}
    {!loading && !rows.length && <div className="card"><h2>No investigations yet</h2><p>Open Investigate, describe an issue, and return here to review the saved result.</p></div>}
    {rows.length > 0 && <div className="results-scroll"><table className="results-table"><caption>Most recent 100 investigations. CSV export contains all saved investigations.</caption><thead><tr>
      {['Bug', 'Expected cause', 'Agent answer', 'Correct?', 'Fix worked?', 'Time taken'].map((title) => <th scope="col" key={title}>{title}</th>)}
    </tr></thead><tbody>{rows.map((row) => <tr key={row.request_id} className={selected?.request_id === row.request_id ? 'selected' : ''}>
      <td><button className="link" onClick={() => choose(row)}>{row.bug}</button><small className="run-source">{row.run_type} | {row.response.pipeline_status}</small></td>
      <td>{row.expected_cause}</td><td><p className="answer-preview">{row.response.answer}</p></td><td>{row.correct}</td><td>{row.fix_worked}</td>
      <td className="nowrap">{row.response.performance?.total_ms.toFixed(1) ?? '--'} ms</td>
    </tr>)}</tbody></table></div>}
    {selected && review && <section className="card review-card" aria-label="Review investigation">
      <h2>Review this result</h2><p className="muted small">{new Date(selected.created_at).toLocaleString()} | {selected.request_id}</p>
      <p className="review-answer">{selected.response.answer}</p>
      <Performance response={{...selected.response, accuracy_recorded: true}} />
      <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <label>Bug or case name<input required maxLength={300} value={review.bug} onChange={(e) => setReview({...review, bug: e.target.value})} /></label>
        <label>Expected cause<textarea aria-label="Expected cause" required maxLength={2000} value={review.expected_cause} onChange={(e) => setReview({...review, expected_cause: e.target.value})} /></label>
        <div className="review-outcomes"><label>Correct?<select value={review.correct} onChange={(e) => setReview({...review, correct: e.target.value as AccuracyReview['correct']})}>{['Not reviewed', 'Yes', 'No', 'Partly'].map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>Fix worked?<select value={review.fix_worked} onChange={(e) => setReview({...review, fix_worked: e.target.value as AccuracyReview['fix_worked']})}>{['Not tested', 'Yes', 'No'].map((value) => <option key={value}>{value}</option>)}</select></label></div>
        <label>Verification notes<textarea aria-label="Verification notes" maxLength={2000} placeholder="What evidence supports your review? What happened after the fix?" value={review.notes} onChange={(e) => setReview({...review, notes: e.target.value})} /></label>
        <p className="muted small">Mark a fix successful only after applying it and repeating the original scenario.</p>
        <button className="btn primary" disabled={busy}>{busy ? 'Saving...' : 'Save review'}</button>
        {notice && <p className="saved-note" role="status">{notice}</p>}
      </form>
    </section>}
  </section>;
}
