import type { CopilotResponse } from '../types';
import RoutingFlow from './RoutingFlow';

export default function Performance({ response }: { response: CopilotResponse }) {
  const report = response.performance;
  if (!report) return null;
  const maximum = Math.max(...report.steps.map((step) => step.latency_ms), 0.001);
  return (
    <section className="card performance" aria-label="Investigation timings">
      <div className="performance-head">
        <div><p className="eyebrow">Automatically selected</p>
          <div className="agent-chips">{response.agents_used.map((agent) => <span key={agent} className={`agent-chip ${agent}`}>{agent}</span>)}</div>
        </div>
        <div><p className="eyebrow">Total time</p><strong className="elapsed">{report.total_ms < 1000 ? `${report.total_ms.toFixed(1)} ms` : `${(report.total_ms / 1000).toFixed(2)} s`}</strong></div>
        <div><p className="eyebrow">Slowest step</p><strong>{report.slowest_step.replace('agent:', '')}</strong></div>
      </div>
      <p className="muted small">{response.analysis?.reason}</p>
      <RoutingFlow response={response} />
      <div className="timing-bars">
        {report.steps.map((step) => <div className="timing-row" key={step.name}>
          <span>{step.name.replace('agent:', '').replace(/_/g, ' ')}{step.parallel && <small> | parallel</small>}</span>
          <div className="timing-track"><span className={step.name === report.slowest_step ? 'bottleneck' : ''} style={{ width: `${Math.max(1, step.latency_ms / maximum * 100)}%` }} /></div>
          <span>{step.latency_ms.toFixed(1)} ms</span>
        </div>)}
      </div>
      <details><summary>Ideas to speed up the slowest step</summary><ul>{report.recommendations.map((idea) => <li key={idea}>{idea}</li>)}</ul></details>
      <p className="muted small">Parallel calls overlap. Total time measures backend investigation work.</p>
      <p className={response.accuracy_recorded ? 'saved-note' : 'error-text'} role="status">
        {response.accuracy_recorded ? 'Saved to accuracy results | ready for review' : 'This result could not be saved to accuracy results.'}
      </p>
    </section>
  );
}
