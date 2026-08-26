import { useEffect, useMemo, useState } from 'react';
import './AgentsView.css';
import { IconPlay, IconTrash, IconClock, IconRefresh, IconSearch, IconPlus, IconSpark } from './icons.jsx';
import {
  createAgent, deleteAgent, getAgent, listAgents, runAgent, setSchedule, toggleSchedule, updateAgent,
} from '../api.js';

const KINDS = [
  { id: 'daily', label: 'Every day' },
  { id: 'weekly', label: 'Every week' },
  { id: 'monthly', label: 'Every month' },
];
const FILTERS = ['All', 'Scheduled', 'Has runs', 'Never run'];

function relTime(epoch) {
  if (!epoch) return null;
  const diff = Date.now() / 1000 - epoch;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export default function AgentsView({ onJobStarted, onView }) {
  const [agents, setAgents] = useState([]);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({ name: '', prompt: '' });
  const [promptEdit, setPromptEdit] = useState('');
  const [sched, setSched] = useState({ kind: 'daily', hour: 9, minute: 0 });
  const [filter, setFilter] = useState('All');
  const [search, setSearch] = useState('');

  const refresh = async () => {
    try {
      const { agents: list } = await listAgents();
      setAgents(list);
      setError('');
    } catch (e) {
      setError(`Backend unreachable (${e.message}).`);
    }
  };

  useEffect(() => { refresh(); }, []);

  const open = async (id) => {
    try {
      const a = await getAgent(id);
      setDetail(a);
      setPromptEdit(a.versions?.[0]?.prompt ?? '');
      if (a.schedule) setSched({ kind: a.schedule.kind, hour: a.schedule.hour, minute: a.schedule.minute });
    } catch (e) { setError(e.message); }
  };

  const create = async () => {
    if (!draft.name.trim() || !draft.prompt.trim()) return;
    setBusy(true);
    try {
      const a = await createAgent({ name: draft.name.trim(), prompt: draft.prompt.trim() });
      setDraft({ name: '', prompt: '' });
      setCreating(false);
      await refresh();
      await open(a.id);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const run = async (id) => {
    setBusy(true);
    try {
      const res = await runAgent(id);
      onJobStarted(res.job_id);
      onView('run');
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const savePrompt = async () => {
    if (!detail || !promptEdit.trim()) return;
    setBusy(true);
    try {
      await updateAgent(detail.id, { prompt: promptEdit.trim(), note: 'Edited in console' });
      await open(detail.id);
      await refresh();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const applySchedule = async () => {
    if (!detail) return;
    setBusy(true);
    try {
      await setSchedule(detail.id, { kind: sched.kind, hour: Number(sched.hour), minute: Number(sched.minute) });
      await open(detail.id);
      await refresh();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const remove = async (id) => {
    setBusy(true);
    try {
      await deleteAgent(id);
      if (detail?.id === id) setDetail(null);
      await refresh();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const shown = useMemo(() => {
    const needle = search.toLowerCase().trim();
    return agents.filter((a) => {
      if (needle && !a.name.toLowerCase().includes(needle)) return false;
      if (filter === 'Scheduled') return !!a.schedule_active;
      if (filter === 'Has runs') return (a.run_count ?? 0) > 0;
      if (filter === 'Never run') return !(a.run_count ?? 0);
      return true;
    });
  }, [agents, filter, search]);

  return (
    <div className="agents-view">
      <header className="agents-head">
        <div>
          <h1 className="agents-h1">Agents</h1>
          <p className="agents-hint">
            A saved agent keeps its own browser session, its version history, and can run on a schedule.
          </p>
        </div>
        <button type="button" className="btn btn-primary" onClick={() => { setCreating(true); setDetail(null); }}>
          <IconPlus /> Create agent
        </button>
      </header>

      <div className="agents-toolbar">
        <div className="agents-search">
          <IconSearch />
          <input className="agents-search-input" placeholder="Search agents…" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <div className="agents-filters">
          {FILTERS.map((f) => (
            <button key={f} type="button" className={`agents-filter ${filter === f ? 'is-active' : ''}`} onClick={() => setFilter(f)}>
              {f}
            </button>
          ))}
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={refresh}><IconRefresh /></button>
      </div>

      {error && <div className="agents-error">{error}</div>}

      <div className="agents-grid">
        {shown.map((a) => (
          <div className="agent-card" key={a.id}>
            <button type="button" className="agent-card-main" onClick={() => open(a.id)}>
              <div className="agent-card-top">
                <span className="agent-card-badge"><IconSpark size={11} /></span>
                <span className="agent-card-name">{a.name}</span>
              </div>
              <p className="agent-card-prompt">{a.latest_version?.prompt ?? 'No prompt yet'}</p>
            </button>
            <div className="agent-card-foot">
              <span className="agent-card-meta">
                v{a.latest_version?.version_number ?? 1}
                {a.schedule_active ? ' · scheduled' : ''}
                {relTime(a.last_run_at) ? ` · ran ${relTime(a.last_run_at)}` : ' · never run'}
              </span>
              <div className="agent-card-actions">
                <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => run(a.id)} title="Run now">
                  <IconPlay />
                </button>
                <button type="button" className="btn btn-danger btn-sm" disabled={busy} onClick={() => remove(a.id)} title="Delete">
                  <IconTrash />
                </button>
              </div>
            </div>
          </div>
        ))}

        {shown.length === 0 && (
          <p className="empty-note">
            {agents.length === 0
              ? 'No agents yet. Create one here, or save a template as an agent from the Templates page.'
              : 'No agents match that filter.'}
          </p>
        )}
      </div>

      {(detail || creating) && (
        <div className="agent-modal-backdrop" onClick={() => { setDetail(null); setCreating(false); }}>
          <div className="agent-modal" onClick={(e) => e.stopPropagation()}>
            {creating ? (
              <>
                <h2 className="agent-modal-title">Create an agent</h2>
                <label className="field-label">Name</label>
                <input className="text-input" placeholder="e.g. Amazon headphone monitor" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                <label className="field-label" style={{ marginTop: 12 }}>What should it do every run?</label>
                <textarea className="text-area" rows={4} placeholder="Go to <url> and extract N records with the fields you want." value={draft.prompt} onChange={(e) => setDraft({ ...draft, prompt: e.target.value })} />
                <div className="agent-modal-actions">
                  <button type="button" className="btn btn-ghost" onClick={() => setCreating(false)}>Cancel</button>
                  <button type="button" className="btn btn-primary" disabled={busy || !draft.name.trim() || !draft.prompt.trim()} onClick={create}>Create</button>
                </div>
              </>
            ) : (
              <>
                <header className="agent-modal-head">
                  <div>
                    <h2 className="agent-modal-title">{detail.name}</h2>
                    <p className="agent-modal-sub mono">session {detail.session}</p>
                  </div>
                  <div className="agent-modal-headactions">
                    <button type="button" className="btn btn-primary" disabled={busy} onClick={() => run(detail.id)}><IconPlay /> Run now</button>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDetail(null)}>Close</button>
                  </div>
                </header>

                <label className="field-label">Prompt (saving creates a new version)</label>
                <textarea className="text-area" rows={4} value={promptEdit} onChange={(e) => setPromptEdit(e.target.value)} />
                <button type="button" className="btn btn-ghost btn-sm agents-save" disabled={busy || promptEdit.trim() === (detail.versions?.[0]?.prompt ?? '')} onClick={savePrompt}>
                  Save as new version
                </button>

                <div className="agent-modal-section">
                  <label className="field-label"><IconClock /> Schedule</label>
                  <div className="agents-sched-row">
                    <select className="text-input" value={sched.kind} onChange={(e) => setSched({ ...sched, kind: e.target.value })}>
                      {KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
                    </select>
                    <input className="text-input" type="number" min="0" max="23" value={sched.hour} onChange={(e) => setSched({ ...sched, hour: e.target.value })} />
                    <span className="agents-colon">:</span>
                    <input className="text-input" type="number" min="0" max="59" value={sched.minute} onChange={(e) => setSched({ ...sched, minute: e.target.value })} />
                    <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={applySchedule}>Set</button>
                  </div>
                  <p className="agents-hint">Times are UTC — that's what the scheduler compares against.</p>
                  {detail.schedule && (
                    <label className="agents-toggle">
                      <input
                        type="checkbox"
                        checked={!!detail.schedule.active}
                        onChange={async (e) => { await toggleSchedule(detail.id, e.target.checked); open(detail.id); refresh(); }}
                      />
                      Active — {detail.schedule.kind} at {String(detail.schedule.hour).padStart(2, '0')}:{String(detail.schedule.minute).padStart(2, '0')} UTC
                    </label>
                  )}
                </div>

                <div className="agent-modal-section">
                  <label className="field-label">Versions</label>
                  <ul className="agents-plain-list">
                    {(detail.versions ?? []).map((v) => (
                      <li key={v.id}><span className="agents-vtag mono">v{v.version_number}</span>{v.note}</li>
                    ))}
                  </ul>
                </div>

                <div className="agent-modal-section">
                  <label className="field-label">Run history</label>
                  {(detail.runs ?? []).length === 0 && <p className="empty-note">No runs yet.</p>}
                  <ul className="agents-plain-list">
                    {(detail.runs ?? []).map((r) => (
                      <li key={r.id}>
                        <span className={`pill pill-${r.status === 'done' ? 'done' : r.status === 'error' ? 'error' : 'running'}`}>{r.status}</span>
                        <span className="agents-run-time">{new Date(r.started_at * 1000).toLocaleString()}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
