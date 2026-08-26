import { useEffect, useMemo, useState } from 'react';
import './TemplatesView.css';
import { IconSearch, IconSpark, IconPlay } from './icons.jsx';
import { listTemplates, startTemplate } from '../api.js';

/**
 * Template gallery. Picking one opens a small form for its inputs rather than
 * running it blind - every template has variables (search term, location, how
 * many) and running with silent defaults is how you get a result for the wrong
 * city and don't notice.
 */
export default function TemplatesView({ onJobStarted, onView }) {
  const [data, setData] = useState({ categories: ['All'], templates: [] });
  const [category, setCategory] = useState('All');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);
  const [values, setValues] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    listTemplates().then(setData).catch((e) => setError(`Backend unreachable (${e.message}).`));
  }, []);

  const shown = useMemo(() => {
    const needle = search.toLowerCase().trim();
    return data.templates.filter((t) => {
      if (category !== 'All' && t.category !== category) return false;
      if (!needle) return true;
      return (
        t.name.toLowerCase().includes(needle) ||
        t.description.toLowerCase().includes(needle) ||
        t.site.toLowerCase().includes(needle)
      );
    });
  }, [data.templates, category, search]);

  const open = (tpl) => {
    setSelected(tpl);
    setValues(Object.fromEntries(tpl.inputs.map((i) => [i.key, i.default])));
    setError('');
  };

  const run = async (save) => {
    if (!selected) return;
    setBusy(true);
    setError('');
    try {
      const res = await startTemplate(selected.id, { values, save });
      onJobStarted(res.job_id);
      onView('run');
      setSelected(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="templates-view">
      <header className="templates-hero">
        <h1 className="templates-title">Templates</h1>
        <p className="templates-sub">
          Starting points that already work. Each one lands straight on the results page and asks for the
          fields worth having — the phrasing is the difference between a run that finishes in 3 steps and one
          that wanders.
        </p>
        <div className="templates-search">
          <IconSearch />
          <input
            className="templates-search-input"
            placeholder="Search templates…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="templates-chips">
          {data.categories.map((c) => (
            <button
              key={c}
              type="button"
              className={`templates-chip ${category === c ? 'is-active' : ''}`}
              onClick={() => setCategory(c)}
            >
              {c}
            </button>
          ))}
        </div>
      </header>

      {error && <div className="templates-error">{error}</div>}

      <div className="templates-grid">
        {shown.map((t) => (
          <button type="button" className="template-card" key={t.id} onClick={() => open(t)}>
            <div className="template-art" style={{ '--accent': t.accent }}>
              <span className="template-site">{t.site}</span>
              <span className="template-name">{t.name}</span>
              <span className="template-steps">{t.est_steps}</span>
            </div>
            <div className="template-body">
              <span className="template-badge"><IconSpark size={11} /> AX</span>
              <p className="template-desc">{t.description}</p>
            </div>
          </button>
        ))}
        {shown.length === 0 && <p className="empty-note">No templates match that.</p>}
      </div>

      {selected && (
        <div className="template-modal-backdrop" onClick={() => setSelected(null)}>
          <div className="template-modal" onClick={(e) => e.stopPropagation()}>
            <header className="template-modal-head">
              <div>
                <h2 className="template-modal-title">{selected.name}</h2>
                <p className="template-modal-site">{selected.site} · {selected.est_steps}</p>
              </div>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSelected(null)}>Close</button>
            </header>

            <p className="template-modal-desc">{selected.description}</p>

            <div className="template-fields">
              {selected.inputs.map((input) => (
                <label className="template-field" key={input.key}>
                  <span className="field-label">{input.label}</span>
                  <input
                    className="text-input"
                    value={values[input.key] ?? ''}
                    onChange={(e) => setValues({ ...values, [input.key]: e.target.value })}
                  />
                </label>
              ))}
            </div>

            {error && <div className="templates-error">{error}</div>}

            <div className="template-modal-actions">
              <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => run(true)}>
                Save as agent &amp; run
              </button>
              <button type="button" className="btn btn-primary" disabled={busy} onClick={() => run(false)}>
                <IconPlay /> {busy ? 'Starting…' : 'Run once'}
              </button>
            </div>
            <p className="template-modal-hint">
              “Save as agent” keeps it in Agents with version history and scheduling. “Run once” just runs it.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
