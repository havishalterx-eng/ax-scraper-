import './Sidebar.css';
import {
  IconAX, IconTerminal, IconRobot, IconHistory, IconBrowser,
  IconPlus, IconGrid, IconShield, IconPlug, IconHome,
} from './icons.jsx';

// Grouped the way the work actually splits: the things you make, the
// infrastructure they run on, and the settings behind both. A flat list of
// nine items reads as a pile; these three groups let you find a page without
// reading every label.
const GROUPS = [
  {
    items: [
      { id: 'home', label: 'Home', Icon: IconHome },
      { id: 'templates', label: 'Templates', Icon: IconGrid },
      { id: 'agents', label: 'Agents', Icon: IconRobot },
    ],
  },
  {
    label: 'Runtime',
    items: [
      { id: 'run', label: 'Console', Icon: IconTerminal },
      { id: 'history', label: 'Runs', Icon: IconHistory },
      { id: 'sessions', label: 'Browsers', Icon: IconBrowser },
    ],
  },
  {
    label: 'Infrastructure',
    items: [
      { id: 'proxies', label: 'Proxies', Icon: IconShield },
      { id: 'integrations', label: 'Delivery', Icon: IconPlug },
    ],
  },
];

function relTime(epoch) {
  if (!epoch) return '';
  const diff = Date.now() / 1000 - epoch;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const STATUS_COLOR = {
  running: 'var(--ax-orange)',
  done: 'var(--ax-success)',
  needs_human: 'var(--ax-warning)',
  error: 'var(--ax-danger)',
};

export default function Sidebar({ view, onView, tasks, activeJobId, onSelectJob, onNewRun, health, onToggleHeadless }) {
  const recent = tasks.slice(0, 8);
  const activeRuns = tasks.filter((t) => t.status === 'running' || t.status === 'needs_human').length;

  return (
    <nav className="sidebar">
      <div className="sidebar-brand">
        <IconAX size={22} />
        <div className="sidebar-brand-text">
          <span className="sidebar-brand-name">AX Scraper</span>
          <span className="sidebar-brand-sub">Console</span>
        </div>
      </div>

      <button type="button" className="btn btn-primary sidebar-new" onClick={onNewRun}>
        <IconPlus /> New run
      </button>

      <div className="sidebar-scroll">
        {GROUPS.map((group, gi) => (
          <div className="sidebar-group" key={group.label ?? gi}>
            {group.label && <span className="sidebar-group-label">{group.label}</span>}
            {group.items.map(({ id, label, Icon }) => (
              <button
                key={id}
                type="button"
                className={`sidebar-item ${view === id ? 'is-active' : ''}`}
                onClick={() => onView(id)}
              >
                <Icon />
                {label}
                {id === 'run' && activeRuns > 0 && <span className="sidebar-badge">{activeRuns}</span>}
              </button>
            ))}
          </div>
        ))}

        <div className="sidebar-group">
          <span className="sidebar-group-label">Recent runs</span>
          {recent.length === 0 && <p className="sidebar-empty">Nothing yet.</p>}
          {recent.map((t) => (
            <button
              key={t.job_id}
              type="button"
              className={`sidebar-run ${activeJobId === t.job_id ? 'is-active' : ''}`}
              onClick={() => onSelectJob(t.job_id)}
              title={t.task}
            >
              <span className="sidebar-run-top">
                <span
                  className={`dot ${t.status === 'running' ? 'dot-pulse' : ''}`}
                  style={{ background: STATUS_COLOR[t.status] ?? 'rgba(255,255,255,0.3)' }}
                />
                <span className="sidebar-run-time">{relTime(t.created_at)}</span>
              </span>
              <span className="sidebar-run-task">{t.task}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="sidebar-foot">
        {health ? (
          <>
            <div className="sidebar-foot-row">
              <span className="dot dot-pulse" style={{ background: 'var(--ax-success)' }} />
              Backend live
              <span className="sidebar-foot-count mono">
                {health.live_sessions} browser{health.live_sessions === 1 ? '' : 's'}
              </span>
            </div>
            <button
              type="button"
              className={`sidebar-headless ${health.headless ? '' : 'is-on'}`}
              onClick={onToggleHeadless}
              title={
                health.headless
                  ? 'Nothing opens on your desktop. Watch runs in the Live browser panel.'
                  : 'A real Chromium window opens for every run. Needed if you want to click things yourself.'
              }
            >
              <span className="sidebar-headless-track"><span className="sidebar-headless-knob" /></span>
              {health.headless ? 'No window' : 'Window shown'}
            </button>
          </>
        ) : (
          <div className="sidebar-foot-row sidebar-foot-down">
            <span className="dot" style={{ background: 'var(--ax-danger)' }} />
            Backend offline
          </div>
        )}
      </div>
    </nav>
  );
}
