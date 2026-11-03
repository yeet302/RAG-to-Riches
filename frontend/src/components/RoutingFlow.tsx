import type { AgentName, CopilotResponse } from '../types';

const duration = (milliseconds: number) => milliseconds >= 1000
  ? `${(milliseconds / 1000).toFixed(2)} s` : `${milliseconds.toFixed(1)} ms`;

function Arrow() {
  return <svg className="route-arrow" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8">
    <path d="M4 12h15M13 6l6 6-6 6" />
  </svg>;
}

export default function RoutingFlow({ response }: { response: CopilotResponse }) {
  const analysis = response.analysis;
  if (!analysis) return null;
  const routeTime = response.performance?.steps.find((step) => step.name === 'routing')?.latency_ms;
  const method = analysis.fast_path ? 'Fast path' : analysis.source === 'llm' ? 'Azure planner'
    : analysis.source === 'keyword_fallback' ? 'Rule fallback' : 'Rule routing';
  const stage = (agents: AgentName[], index: number) => (
    <div className={`route-stage${agents.length > 1 ? ' parallel-stage' : ''}`} role="group" aria-label={`Stage ${index}${agents.length > 1 ? ': parallel' : ': one agent'}`}>
      <p className="route-caption">Stage {index}</p>
      <p className="route-stage-mode">{agents.length > 1 ? 'Run in parallel' : 'One agent'}{index === 2 ? ' after Stage 1' : ''}</p>
      <div className="route-agent-stack">
        {agents.map((agent) => {
          const timing = response.performance?.steps.find((step) => step.name === `agent:${agent}`)?.latency_ms
            ?? response.trace.find((event) => event.stage === index && event.agent === agent)?.latency_ms;
          const slowest = response.performance?.slowest_step === `agent:${agent}`;
          const unavailable = response.errors.some((error) => error.agent === agent);
          const count = response.evidence.filter((item) => item.agent === agent).length;
          return <div className={`route-agent${slowest ? ' route-slowest' : ''}`} key={agent}>
            <div><span className={`agent-chip ${agent}`}>{agent}</span>{slowest && <span className="route-slowest-label">Slowest</span>}</div>
            <strong>{timing == null ? 'Not measured' : duration(timing)}</strong>
            <small>{unavailable ? 'Needs attention' : `${count} source${count === 1 ? '' : 's'} returned`}</small>
          </div>;
        })}
      </div>
    </div>
  );
  return <section className="routing-flow" aria-label="Agent routing">
    <h3>Agent routing</h3>
    <div className="routing-path">
      <div className="route-endpoint"><p className="route-caption">Route decision</p><strong>{method}</strong>
        <small>{analysis.fast_path ? 'No planning model call' : 'Select agents and order'}</small>
        {routeTime != null && <small>{duration(routeTime)}</small>}
      </div>
      <Arrow />
      {stage(analysis.stage1, 1)}
      {analysis.stage2.length > 0 && <><Arrow />{stage(analysis.stage2, 2)}</>}
      <Arrow />
      <div className="route-endpoint route-result"><p className="route-caption">Combine</p>
        <strong>{response.synthesis?.source === 'llm' ? 'AI answer' : 'Evidence result'}</strong>
        <small>From selected sources</small>
      </div>
    </div>
    <p className="muted small">{analysis.stage2.length > 0
      ? 'Stage 2 receives Stage 1 findings before it starts.'
      : analysis.stage1.length > 1 ? 'Agents in the same stage run together.' : 'Only the selected specialist was called.'}</p>
  </section>;
}
