import { useState } from 'react';

interface Props {
  onSubmit: (question: string) => void;
  busy: boolean;
  compact?: boolean;
}

export default function Composer({ onSubmit, busy, compact }: Props) {
  const [text, setText] = useState('');

  const submit = (e?: React.FormEvent) => {
    e?.preventDefault();
    const q = text.trim();
    if (!q || busy) return;
    onSubmit(q);
    setText('');
  };

  return (
    <form className={`composer${compact ? ' compact' : ''}`} onSubmit={submit}>
      <label htmlFor="question" className="sr-only">Describe a problem or ask a question</label>
      <input
        id="question"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={compact ? 'Ask a follow-up or describe another problem' : 'Describe a problem or paste an error'}
        autoComplete="off"
        maxLength={2000}
      />
      <button type="submit" className="btn primary" disabled={busy || !text.trim()}>
        {busy ? 'Investigating…' : compact ? 'Ask' : 'Investigate'}
      </button>
    </form>
  );
}
