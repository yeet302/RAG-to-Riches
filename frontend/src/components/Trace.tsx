import { duration } from '../duration';
import type { AgentName, TraceEvent, PerformanceSummary } from '../types';

const STAGE: Record<number, string> = {
  0: 'Route',
  1: 'Stage 1',
  2: 'Stage 2',
  3: 'Combine',
  4: 'Answer',
};

export default function Trace({ trace, agents, performance }: { trace: TraceEvent[]; agents: AgentName[]; performance?: PerformanceSummary | null }) {
  return (
    <details className="trace">
      <summary>
        How Copilot investigated
        <span className="agent-chips">
          {agents.map((a) => (
            <span key={a} className={`agent-chip ${a}`}>{a}</span>
          ))}
        </span>
      </summary>
      {performance && (
        <p>Total: {duration(performance.total_ms)}. Slowest step: {performance.slowest_step}.</p>
      )}
      <ol>
        {trace.map((t, i) => (
          <li key={i}>
            <span className="stage">{STAGE[t.stage] ?? `Stage ${t.stage}`}</span>
            <span className="trace-agent">
              {t.agent}
              {t.parallel && <span className="parallel"> · parallel</span>}
            </span>
            <span className="trace-action">{t.action} <small>({duration(t.latency_ms)})</small></span>
          </li>
        ))}
      </ol>
    </details>
  );
}
