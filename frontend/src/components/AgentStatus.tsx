import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import type { AgentStatusReport } from '../types';

const STATE_LABEL = { ready: 'Ready', degraded: 'Needs attention', offline: 'Offline', local: 'Local search' } as const;
const REFRESH_MS = 30_000;

interface Props {
  /** Bump this after each investigation so the status reflects the latest run. */
  refreshKey: number;
}

/** Header indicator: how many agents are ready, with a panel explaining each one. */
export default function AgentStatus({ refreshKey }: Props) {
  const [report, setReport] = useState<AgentStatusReport | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    api.agentStatus().then((r) => { setReport(r); setFailed(false); }).catch(() => setFailed(true));
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load, refreshKey]);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape'
        : !panelRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close); };
  }, [open]);

  const level = failed || !report ? 'unknown'
    : report.agents.some((a) => a.state === 'offline') ? 'bad'
    : report.agents.some((a) => a.state === 'degraded') ? 'warn' : 'good';
  const label = failed ? 'Agents: no status' : !report ? 'Checking agents…'
    : `${report.ready}/${report.total} agents ready`;

  return (
    <div className="agent-status" ref={panelRef}>
      <button className={`pill agent-pill ${level}`} onClick={() => setOpen(!open)} aria-expanded={open}
        aria-haspopup="dialog" title="Agent status">
        <span className="status-dot" aria-hidden />{label}
      </button>
      {open && (
        <div className="agent-panel" role="dialog" aria-label="Agent status">
          <div className="agent-panel-head">
            <b>Agent status</b>
            <button className="link small" onClick={load}>Refresh</button>
          </div>
          {failed && <p className="muted small">Couldn't reach the orchestrator to check the agents.</p>}
          {report?.agents.map((agent) => (
            <div key={agent.name} className="agent-row">
              <span className={`status-dot ${agent.state}`} aria-hidden />
              <div>
                <div className="agent-row-head">
                  <b>{agent.name}</b>
                  <span className={`agent-state ${agent.state}`}>{STATE_LABEL[agent.state]}</span>
                  {agent.latency_ms != null && <span className="muted small">{agent.latency_ms} ms</span>}
                </div>
                <p className="muted small">{agent.detail}</p>
                {agent.state === 'degraded' && agent.last_run?.message && (
                  <p className="small agent-error">{agent.last_run.message}</p>
                )}
              </div>
            </div>
          ))}
          {report && (
            <p className="muted small agent-panel-foot">
              "Ready" means the service answers and its last question didn't fail. Checked{' '}
              {new Date(report.checked_at).toLocaleTimeString()}.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
