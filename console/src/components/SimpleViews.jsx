import { useEffect, useState } from 'react';
import './SimpleViews.css';
import { IconRefresh, IconTrash } from './icons.jsx';
import { callTool, listSessions, listTasks } from '../api.js';

export function SessionsView() {
  const [sessions, setSessions] = useState([]);
  const [error, setError] = useState('');

  const refresh = async () => {
    try {
      const { sessions: list } = await listSessions();
      setSessions(list);
      setError('');
    } catch (err) { setError(`Backend unreachable (${err.message}).`); }
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  const close = async (name) => {
    try {
      await callTool('close_browser', { session: name });
      refresh();
    } catch (err) { setError(err.message); }
  };

  return (
    <div className="simple-view">
      <header className="simple-head">
        <h2 className="simple-title">Browser sessions</h2>
        <button type="button" className="btn btn-ghost btn-sm" onClick={refresh}><IconRefresh /> Refresh</button>
      </header>
      <p className="simple-hint">
        Each session is a separate real Chromium with its own cookies and fingerprint. Closing one frees its memory; a
        signed-in session keeps its profile on disk and resumes logged in next time.
      </p>
      {error && <div className="simple-error">{error}</div>}
      {sessions.length === 0 && <p className="empty-note">No browser sessions open right now.</p>}
      <div className="simple-cards">
        {sessions.map((s) => (
          <div className="simple-card" key={s.name}>
            <div className="simple-card-main">
              <span className="simple-card-title mono">{s.name}</span>
              <span className="simple-card-sub mono">{s.url || '(no page loaded)'}</span>
            </div>
            {s.persistent && <span className="pill pill-warn">signed-in</span>}
            <button type="button" className="btn btn-danger btn-sm" onClick={() => close(s.name)}>
              <IconTrash /> Close
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

export function HistoryView({ onSelectJob, onView }) {
  const [tasks, setTasks] = useState([]);
  const [error, setError] = useState('');

  const refresh = async () => {
    try {
      const { tasks: list } = await listTasks();
      setTasks(list);
      setError('');
    } catch (err) { setError(`Backend unreachable (${err.message}).`); }
  };

  useEffect(() => { refresh(); }, []);

  return (
    <div className="simple-view">
      <header className="simple-head">
        <h2 className="simple-title">Run history</h2>
        <button type="button" className="btn btn-ghost btn-sm" onClick={refresh}><IconRefresh /> Refresh</button>
      </header>
      <p className="simple-hint">Every run, including ones from before the last restart — those are read back from disk.</p>
      {error && <div className="simple-error">{error}</div>}
      {tasks.length === 0 && <p className="empty-note">No runs recorded yet.</p>}
      <div className="simple-cards">
        {tasks.map((t) => (
          <button
            key={t.job_id}
            type="button"
            className="simple-card simple-card-btn"
            onClick={() => { onSelectJob(t.job_id); onView('run'); }}
          >
            <div className="simple-card-main">
              <span className="simple-card-title">{t.task}</span>
              <span className="simple-card-sub mono">
                {t.created_at ? new Date(t.created_at * 1000).toLocaleString() : ''} · {t.session}
                {t.from_history ? ' · from disk' : ''}
              </span>
            </div>
            <span className={`pill pill-${t.status === 'done' ? 'done' : t.status === 'running' ? 'running' : t.status === 'error' ? 'error' : 'idle'}`}>
              {String(t.status).replace('_', ' ')}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
