import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { Incident, Message } from './types';
import Composer from './components/Composer';
import IncidentList from './components/IncidentList';
import Thread from './components/Thread';

const uid = () => Math.random().toString(36).slice(2, 10);

export default function App() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [incidentsError, setIncidentsError] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const conversationId = useRef(uid());
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.incidents().then(setIncidents).catch((e) => setIncidentsError(String(e.message ?? e)));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  const ask = useCallback(
    async (question: string, incident?: Incident) => {
      const q = question.trim();
      if (!q || busy) return;
      const pendingId = uid();
      setBusy(true);
      setMessages((m) => [
        ...m,
        { id: uid(), role: 'user', text: q, incident },
        { id: pendingId, role: 'assistant', status: 'loading', question: q },
      ]);
      try {
        const response = await api.ask(q, incident?.id, conversationId.current);
        setMessages((m) => m.map((x) => (x.id === pendingId ? { id: pendingId, role: 'assistant', status: 'done', response } : x)));
      } catch (e) {
        const error = e instanceof Error ? e.message : String(e);
        setMessages((m) => m.map((x) => (x.id === pendingId ? { id: pendingId, role: 'assistant', status: 'error', error, question: q } : x)));
      } finally {
        setBusy(false);
      }
    },
    [busy],
  );

  const goHome = () => {
    setMessages([]);
    conversationId.current = uid();
  };

  const inChat = messages.length > 0;

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <button className="brand" onClick={goHome} aria-label="Incident Copilot home">
            <span className="logo" aria-hidden>
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 12h4l2-5 4 10 2-5h6" />
              </svg>
            </span>
            <span className="brand-name">Incident Copilot</span>
          </button>
          {inChat ? (
            <button className="link back" onClick={goHome}>← All incidents</button>
          ) : (
            <span className="team">Ads team</span>
          )}
          <span className="spacer" />
          <span className="pill demo" title="Answers are hard-coded demo responses until the agents are connected">Demo responses</span>
          <span className="pill oncall">You're on call</span>
        </div>
      </header>

      <main className="page">
        {!inChat && <h1 className="hero">What's going on?</h1>}
        <Composer onSubmit={(q) => ask(q)} busy={busy} compact={inChat} />

        {inChat ? (
          <>
            <Thread messages={messages} onAsk={(q) => ask(q)} conversationId={conversationId.current} />
            <div ref={bottomRef} />
          </>
        ) : (
          <IncidentList incidents={incidents} error={incidentsError} onOpen={(inc) => ask(inc.question, inc)} />
        )}
      </main>
    </div>
  );
}
