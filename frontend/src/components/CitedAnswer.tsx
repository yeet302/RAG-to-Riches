import type { Evidence } from '../types';

interface Props {
  text: string;
  evidence: Evidence[];
  onSource: (ref: string) => void;
}

export default function CitedAnswer({ text, evidence, onSource }: Props) {
  const references = new Map<string, { number: number; ref: string }>();
  evidence.forEach((item, index) => {
    const marker = `[${item.ref}]`;
    if (!references.has(marker)) references.set(marker, { number: index + 1, ref: item.ref });
  });
  const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = references.size ? new RegExp(`(${[...references.keys()].sort((a, b) => b.length - a.length).map(escape).join('|')})`, 'g') : null;
  return <div className="answer-text cited-answer">
    {text.split(/\n\s*\n/).map((paragraph, paragraphIndex) => <p key={paragraphIndex}>
      {(pattern ? paragraph.split(pattern) : [paragraph]).map((part, index) => {
        const source = references.get(part);
        return source ? <button type="button" key={index} className="source-citation"
          aria-label={`Show source ${source.number}`} title={source.ref} onClick={() => onSource(source.ref)}>[{source.number}]</button> : part;
      })}
    </p>)}
  </div>;
}
