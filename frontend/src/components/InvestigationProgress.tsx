import { useEffect, useState } from 'react';
import type { AgentName, InvestigationProgress as ProgressEvent } from '../types';

const labels: Record<AgentName, string> = { logs: 'Logs', docs: 'Documentation', code: 'Source code' };
const tips = ['Following the clues, one source at a time.', 'Good answers start with evidence.', 'Connecting the dots across your services.'];

export default function InvestigationProgress({ events }: { events: ProgressEvent[] }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const start = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const agents = events.find((event) => event.agents)?.agents ?? [];
  const synthesizing = events.some((event) => event.phase === 'synthesis');
  const planned = events.some((event) => event.phase === 'collecting');
  const statuses = Object.fromEntries(events.filter((event) => event.agent).map((event) => [event.agent, event.status]));
  const finished = agents.filter((agent) => statuses[agent] && statuses[agent] !== 'running').length;
  const completed = (planned ? 1 : 0) + finished;
  const total = agents.length + 2;
  const percent = Math.round(completed / total * 100);
  const title = synthesizing ? 'Connecting the dots' : planned ? 'Following the evidence' : 'Charting the investigation';
  return (
    <section className="card investigation-progress" aria-label="Investigation progress">
      <div className="investigation-heading">
        <span className="investigation-orbit" aria-hidden="true">✦</span>
        <div><p className="investigation-eyebrow">COPILOT IS ON THE CASE</p><h2>{title}</h2></div>
        <span className="investigation-clock">{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')}</span>
      </div>
      <div className="investigation-track" role="progressbar" aria-label="Investigation stages completed"
        aria-valuemin={0} aria-valuemax={total} aria-valuenow={completed}
        aria-valuetext={`${completed} of ${total} stages complete`}>
        <div className="investigation-fill" style={{ width: `${Math.max(4, percent)}%` }} />
      </div>
      <p className="investigation-caption">{completed} of {total} stages complete <span>Live updates <i /></span></p>
      <ol className="investigation-stages">
        <li data-state={planned ? 'done' : 'active'}><span className="stage-icon">{planned ? '✓' : '◌'}</span><span>Plan the investigation</span><small>{planned ? 'Ready' : 'Working'}</small></li>
        {agents.map((agent) => {
          const status = statuses[agent];
          const done = status && status !== 'running';
          const warning = done && status !== 'ok';
          return <li key={agent} data-state={warning ? 'warning' : done ? 'done' : status === 'running' ? 'active' : 'waiting'}>
            <span className="stage-icon">{warning ? '!' : done ? '✓' : status === 'running' ? '◌' : '·'}</span>
            <span>{labels[agent]}</span><small>{status === 'running' ? 'Searching' : status === 'no_evidence' ? 'No matches' : warning ? 'Needs attention' : done ? 'Collected' : 'Queued'}</small>
          </li>;
        })}
        <li data-state={synthesizing ? 'active' : 'waiting'}><span className="stage-icon">{synthesizing ? '◌' : '·'}</span><span>Prepare your answer</span><small>{synthesizing ? 'Working' : 'Up next'}</small></li>
      </ol>
      <p className="investigation-tip">✧ {elapsed >= 45 ? 'Still investigating. Some sources take a little longer to respond.' : tips[Math.floor(elapsed / 8) % tips.length]}</p>
    </section>
  );
}
