import { useEffect, useState } from 'react';
import './HomeView.css';
import { IconPlay, IconSpark, IconArrowRight } from './icons.jsx';
import { createTask, listTemplates, startTemplate } from '../api.js';

const SUGGESTIONS = [
  'Go to https://news.ycombinator.com/ and extract 30 records with titles, points and links.',
  'Go to https://www.amazon.in/s?k=running+shoes and extract 50 records with price and rating.',
  'Go to https://old.reddit.com/r/startups/hot/ and extract 25 posts with titles, scores and links.',
];

export default function HomeView({ onJobStarted, onView, tasks, health }) {
  const [prompt, setPrompt] = useState('');
  const [persistent, setPersistent] = useState(false);
  const [templates, setTemplates] = useState([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listTemplates()
      .then((d) => setTemplates(d.templates.filter((t) => t.id !== 'custom').slice(0, 4)))
      .catch(() => { /* the sidebar already reports a dead backend */ });
  }, []);

  const start = async () => {
    const task = prompt.trim();
    if (!task) return;
    setBusy(true);
    setError('');
    try {
      const res = await createTask(task, `run-${Math.random().toString(36).slice(2, 8)}`, persistent);
      onJobStarted(res.job_id);
      onView('run');
      setPrompt('');
    } catch (e) {
      setError(`Couldn't reach the backend (${e.message}). Is it running on :8787?`);
    } finally {
      setBusy(false);
    }
  };

  const runTemplate = async (tpl) => {
    setBusy(true);
    try {
      const res = await startTemplate(tpl.id, { values: {} });
      onJobStarted(res.job_id);
      onView('run');
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const done = tasks.filter((t) => t.status === 'done').length;

  return (
    <div className="home-view">
      <section className="home-hero">
        <h1 className="home-title">
          Scrape any <span className="home-title-hl">data</span> from any site.
        </h1>
        <p className="home-sub">Describe what you need. Watch it happen. Keep the agent.</p>

        <div className="home-composer">
          <textarea
            className="home-composer-input"
            rows={3}
            placeholder="Describe the data you want, and the page it's on."
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) start(); }}
          />
          <div className="home-composer-bar">
            <label className="home-check">
              <input type="checkbox" checked={persistent} onChange={(e) => setPersistent(e.target.checked)} />
              Signed-in session
            </label>
            <span className="home-composer-meta mono">
              {health ? (health.headless ? 'no window' : 'window shown') : 'offline'}
            </span>
            <button type="button" className="btn btn-primary home-build" disabled={!prompt.trim() || busy} onClick={start}>
              <IconPlay /> {busy ? 'Starting…' : 'Run'}
            </button>
          </div>
        </div>

        <div className="home-suggestions">
          {SUGGESTIONS.map((s) => (
            <button key={s} type="button" className="home-suggestion" onClick={() => setPrompt(s)}>
              {s}
            </button>
          ))}
        </div>

        {error && <div className="home-error">{error}</div>}
      </section>

      <section className="home-section">
        <header className="home-section-head">
          <h2 className="home-section-title">Start with a template</h2>
          <button type="button" className="home-browse" onClick={() => onView('templates')}>
            Browse all <IconArrowRight size={13} />
          </button>
        </header>
        <div className="home-template-row">
          {templates.map((t) => (
            <button type="button" className="home-template" key={t.id} onClick={() => runTemplate(t)} style={{ '--accent': t.accent }}>
              <span className="home-template-site">{t.site}</span>
              <span className="home-template-name">{t.name}</span>
              <span className="home-template-foot"><IconSpark size={11} /> {t.est_steps}</span>
            </button>
          ))}
          {templates.length === 0 && <p className="empty-note">Templates load from the backend — start it to see them.</p>}
        </div>
      </section>

      <section className="home-section">
        <header className="home-section-head">
          <h2 className="home-section-title">Your activity</h2>
          <button type="button" className="home-browse" onClick={() => onView('history')}>
            All runs <IconArrowRight size={13} />
          </button>
        </header>
        <div className="home-stats">
          <div className="home-stat"><span className="home-stat-value">{tasks.length}</span>Runs recorded</div>
          <div className="home-stat"><span className="home-stat-value">{done}</span>Completed</div>
          <div className="home-stat">
            <span className="home-stat-value">{health?.live_sessions ?? '—'}</span>Browsers open
          </div>
          <div className="home-stat">
            <span className="home-stat-value mono home-stat-small">{health?.model?.split('.').pop() ?? '—'}</span>Model
          </div>
        </div>
      </section>
    </div>
  );
}
