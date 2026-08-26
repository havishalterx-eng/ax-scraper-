import { useEffect, useRef, useState } from 'react';
import './ActivityLog.css';

const LABEL = {
  user: 'You',
  user_queued: 'You (queued)',
  model: 'Agent thinking',
  tool_call: 'Action',
  tool_result: 'Result',
  final: 'Answer',
  system: 'System',
};

function summarise(entry) {
  if (entry.type === 'tool_call') {
    const args = { ...(entry.args || {}) };
    delete args.session;
    delete args.persistent;
    const argText = Object.entries(args)
      .map(([k, v]) => `${k}=${String(v).slice(0, 70)}`)
      .join(' ');
    return `${entry.name}(${argText})`;
  }
  return entry.text || '';
}

function timeOf(entry) {
  if (!entry.at) return '';
  return new Date(entry.at * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

export default function ActivityLog({ log, active }) {
  const [expanded, setExpanded] = useState({});
  const endRef = useRef(null);
  const wrapRef = useRef(null);
  const stickRef = useRef(true);

  // Only auto-scroll when the user is already at the bottom - yanking the view
  // down while they are reading an earlier step makes a long run unreadable.
  const onScroll = () => {
    const el = wrapRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  };

  useEffect(() => {
    if (stickRef.current) endRef.current?.scrollIntoView({ block: 'end' });
  }, [log.length]);

  if (!log.length) {
    return <p className="empty-note">Nothing yet. Start a run and every real step shows up here.</p>;
  }

  return (
    <div className="activity-log" ref={wrapRef} onScroll={onScroll}>
      {log.map((entry, i) => {
        const text = summarise(entry);
        const long = text.length > 260;
        const isOpen = expanded[i];
        return (
          <div key={i} className={`log-row log-${entry.type}${entry.is_error ? ' log-failed' : ''}`}>
            <div className="log-meta">
              <span className="log-kind">{LABEL[entry.type] || entry.type}</span>
              <span className="log-time mono">{timeOf(entry)}</span>
            </div>
            <div className={`log-text mono${long && !isOpen ? ' log-clamped' : ''}`}>
              {long && !isOpen ? `${text.slice(0, 260)}…` : text}
            </div>
            {long && (
              <button
                type="button"
                className="log-toggle"
                onClick={() => setExpanded((e) => ({ ...e, [i]: !e[i] }))}
              >
                {isOpen ? 'Show less' : `Show all ${text.length} chars`}
              </button>
            )}
          </div>
        );
      })}
      {active && (
        <div className="log-row log-pending">
          <span className="dot dot-pulse" style={{ background: 'var(--ax-orange)' }} />
          <span className="log-text">Working…</span>
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}
