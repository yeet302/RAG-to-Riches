import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { HealthResponse, Incident, Message } from './types';
import Composer from './components/Composer';
import IncidentList from './components/IncidentList';
import Thread from './components/Thread';
import AccuracyResults from './components/AccuracyResults';

const uid = () => Math.random().toString(36).slice(2, 10);

const starterQuestions = [
  { topic: 'Documentation', question: 'Show documentation for building the OpenTelemetry Demo Ad Service locally.' },
  { topic: 'Source code', question: 'Where is GetAds implemented in the OpenTelemetry Demo Ad Service?' },
  { topic: 'Logs', question: 'Show Ad Service warning logs from the last hour.' },
  { topic: 'Full investigation', question: 'Investigate why Ad Service GetAds requests return UNAVAILABLE using logs, documentation, and source code.' },
];

export default function App() {
  const [view, setView] = useState<'investigate' | 'accuracy'>('investigate');
  const [sourceNote, setSourceNote] = useState('');
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [incidentsError, setIncidentsError] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [followups, setFollowups] = useState<string[]>([]);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [suggestionsUnavailable, setSuggestionsUnavailable] = useState(false);
  const suggestionVersion = useRef(0);
  const recentQuestions = useRef<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<HealthResponse['mode'] | null>(null);
  const conversationId = useRef(uid());
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.incidents().then(setIncidents).catch((e) => setIncidentsError(String(e.message ?? e)));
    api.health().then((health) => {
      setMode(health.mode);
      setSourceNote(health.source_note ?? '');
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
      const version = ++suggestionVersion.current;
      setFollowups([]);
      setSuggestionsUnavailable(false);
      setSuggestionsLoading(false);
      setBusy(true);
      setMessages((m) => [
        ...m,
        { id: uid(), role: 'user', text: q, incident },
        { id: pendingId, role: 'assistant', status: 'loading', question: q },
      ]);
      try {
        const response = await api.askStream(q, incident?.id, conversationId.current, (event) => {
          setMessages((messages) => messages.map((message) =>
            message.id === pendingId && message.role === 'assistant' && message.status === 'loading'
              ? { ...message, progress: [...(message.progress ?? []), event] } : message));
        });
        setMessages((m) => m.map((x) => (x.id === pendingId ? { id: pendingId, role: 'assistant', status: 'done', response } : x)));
        if (suggestionVersion.current === version) {
          const previous = recentQuestions.current;
          recentQuestions.current = [...previous, q].slice(-3);
          setSuggestionsLoading(true);
          void api.followups(response, previous).then((result) => {
            if (suggestionVersion.current !== version) return;
            setFollowups(result.questions);
            setSuggestionsUnavailable(result.source !== 'llm' || !result.questions.length);
          }).catch(() => {
            if (suggestionVersion.current === version) setSuggestionsUnavailable(true);
          }).finally(() => {
            if (suggestionVersion.current === version) setSuggestionsLoading(false);
          });
        }
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
    ++suggestionVersion.current;
    recentQuestions.current = [];
    setFollowups([]);
    setSuggestionsLoading(false);
    setSuggestionsUnavailable(false);
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
        <div className={`query-workspace${inChat ? ' compact' : ''}`}>
          <Composer onSubmit={(q) => ask(q)} busy={busy} compact={inChat} />
          <section className="query-suggestions" aria-label={inChat ? 'Follow-up questions' : 'Example investigations'}>
            <div className="query-suggestions-heading">
              <span>{inChat ? 'Keep investigating' : 'Choose a starting point'}</span>
              {followups.length > 0 && <span className="suggestion-ai">✦ AI follow-ups</span>}
            </div>
            {!inChat ? <div className="query-starters">
              {starterQuestions.map(({ topic, question }) => <button type="button" className="query-starter" key={topic} disabled={busy} onClick={() => ask(question)}>
                <span>{topic} <span aria-hidden="true">↗</span></span><small>{question}</small>
              </button>)}
            </div> : <>
              <p className="muted small" role="status">{busy ? 'Follow-up ideas will appear here when the investigation finishes.' : suggestionsLoading ? 'Thinking of useful next questions…' : suggestionsUnavailable ? 'AI suggestions are unavailable. Ask your own follow-up above.' : !followups.length ? 'Ask another question to continue investigating.' : ''}</p>
              {followups.length > 0 && <div className="query-followups">{followups.map((question) =>
                <button type="button" className="query-followup" key={question} disabled={busy} onClick={() => ask(question)}><span aria-hidden="true">↳</span>{question}</button>)}</div>}
            </>}
          </section>
        </div>

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
