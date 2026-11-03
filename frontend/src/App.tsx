import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { AnswerMode, HealthResponse, Incident, Message, RoutingMode } from './types';
import Composer from './components/Composer';
import IncidentList from './components/IncidentList';
import Thread from './components/Thread';
import AccuracyResults from './components/AccuracyResults';

const uid = () => Math.random().toString(36).slice(2, 10);

export default function App() {
  const [view, setView] = useState<'investigate' | 'accuracy'>('investigate');
  const [sourceNote, setSourceNote] = useState('');
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [incidentsError, setIncidentsError] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<HealthResponse['mode'] | null>(null);
  const [routingMode, setRoutingMode] = useState<RoutingMode>('keyword');
  const [llmConfigured, setLlmConfigured] = useState(false);
  const [answerMode, setAnswerMode] = useState<AnswerMode>('evidence');
  const conversationId = useRef(uid());
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.incidents().then(setIncidents).catch((e) => setIncidentsError(String(e.message ?? e)));
    api.health().then((health) => {
      setMode(health.mode);
      setSourceNote(health.source_note ?? '');
      setLlmConfigured(health.llm_configured);
      setAnswerMode(health.answer_mode ?? (health.llm_configured ? 'llm' : 'evidence'));
      setRoutingMode(!health.llm_configured ? 'keyword' : health.routing_mode ?? 'keyword');
    }).catch(() => setMode(null));
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
        const response = await api.ask(q, incident?.id, conversationId.current,
          mode === 'local' ? routingMode : undefined,
          mode === 'local' ? answerMode : undefined);
        setMessages((m) => m.map((x) => (x.id === pendingId ? { id: pendingId, role: 'assistant', status: 'done', response } : x)));
      } catch (e) {
        const error = e instanceof Error ? e.message : String(e);
        setMessages((m) => m.map((x) => (x.id === pendingId ? { id: pendingId, role: 'assistant', status: 'error', error, question: q } : x)));
      } finally {
        setBusy(false);
      }
    },
    [busy, mode, routingMode, answerMode],
  );

  const goHome = () => {
    setView('investigate');
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
          <nav className="view-tabs" aria-label="Workspace">
            <button aria-pressed={view === 'investigate'} onClick={() => setView('investigate')}>Investigate</button>
            <button aria-pressed={view === 'accuracy'} onClick={() => setView('accuracy')}>Accuracy results</button>
          </nav>
          {mode && (
            <span className="pill">
              {mode === 'remote' ? 'Remote agents' : 'Local search'}
            </span>
          )}
          <span className="pill oncall">You're on call</span>
        </div>
      </header>

      <main className={view === 'accuracy' ? 'page results-page' : 'page'}>
        {view === 'accuracy' ? <AccuracyResults /> : <>
        {sourceNote && <p className="source-notice">{sourceNote}</p>}
        {!inChat && <h1 className="hero">What's going on?</h1>}
        {mode === 'local' && !sourceNote.startsWith('Offline walkthrough') && <>
        <div className="analysis-controls">
          <label htmlFor="routing-mode">Question analysis</label>
          <select
            id="routing-mode"
            value={routingMode}
            onChange={(event) => setRoutingMode(event.target.value as RoutingMode)}
            disabled={busy}
          >
            <option value="keyword">Keywords</option>
            <option value="llm" disabled={!llmConfigured}>Azure AI{!llmConfigured ? ' (not configured)' : ''}</option>
          </select>
          <span className="muted small">
            {routingMode === 'llm' ? 'AI plans specialist questions and tasks.' : 'Rules select the route.'}
          </span>
        </div>
        <div className="analysis-controls">
          <label htmlFor="answer-mode">Answer generation</label>
          <select
            id="answer-mode"
            value={answerMode}
            onChange={(event) => setAnswerMode(event.target.value as AnswerMode)}
            disabled={busy}
          >
            <option value="llm" disabled={!llmConfigured}>AI explanation{!llmConfigured ? ' (not configured)' : ''}</option>
            <option value="evidence">Evidence list</option>
          </select>
          <span className="muted small">
            {answerMode === 'llm' ? 'Azure explains the collected evidence with citations.' : 'Show matching excerpts without AI answer generation.'}
          </span>
        </div>
        </>}
        {!inChat && <div className="example-questions" aria-label="Example investigations">
          <p className="muted small">Try a plain-language question</p>
          {['Where is the investigate function defined?', 'How does the orchestrator route requests?', 'Show ad service warning logs', 'The ad service is returning errors. Why?'].map((question) =>
            <button className="chip" key={question} disabled={busy} onClick={() => ask(question)}>{question}</button>)}
        </div>}
        <Composer onSubmit={(q) => ask(q)} busy={busy} compact={inChat} />

        {inChat ? (
          <>
            <Thread messages={messages} onAsk={(q) => ask(q)} conversationId={conversationId.current} />
            <div ref={bottomRef} />
          </>
        ) : (
          <IncidentList incidents={incidents} error={incidentsError} onOpen={(inc) => ask(inc.question, inc)} />
        )}
        </>}
      </main>
    </div>
  );
}
