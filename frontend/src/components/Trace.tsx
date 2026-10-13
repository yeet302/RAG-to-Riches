import type { AgentName, TraceEvent } from '../types';

const STAGE: Record<number, string> = {
  0: 'Route',
  1: 'Stage 1',
  2: 'Stage 2',
  3: 'Combine',
};

export default function Trace({ trace, agents }: { trace: TraceEvent[]; agents: AgentName[] }) {
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
      <ol>
        {trace.map((t, i) => (
          <li key={i}>
            <span className="stage">{STAGE[t.stage] ?? `Stage ${t.stage}`}</span>
            <span className="trace-agent">
              {t.agent}
              {t.parallel && <span className="parallel"> · parallel</span>}
            </span>
            <span className="trace-action">{t.action}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}
