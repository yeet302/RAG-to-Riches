import { useLayoutEffect, useRef, useState } from 'react';

interface Props {
  onSubmit: (question: string) => void;
  busy: boolean;
  compact?: boolean;
}

export default function Composer({ onSubmit, busy, compact }: Props) {
  const [text, setText] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    const resize = () => {
      input.style.height = 'auto';
      input.style.height = `${input.scrollHeight + input.offsetHeight - input.clientHeight}px`;
    };
    resize();
    let width = input.clientWidth;
    const observer = new ResizeObserver(() => {
      if (input.clientWidth !== width) {
        width = input.clientWidth;
        resize();
      }
    });
    observer.observe(input);
    return () => observer.disconnect();
  }, [text]);

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
      <textarea
        ref={inputRef}
        rows={1}
        aria-describedby="question-hint"
        id="question"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder={compact ? 'Ask a follow-up or describe another problem' : 'Describe a problem or paste an error'}
        autoComplete="off"
        maxLength={2000}
      />
      <span id="question-hint" className="sr-only">Enter to send. Shift+Enter for a new line.</span>
      <button type="submit" className="btn primary" disabled={busy || !text.trim()}>
        {busy ? 'Investigating…' : compact ? 'Ask' : 'Investigate'}
      </button>
    </form>
  );
}
