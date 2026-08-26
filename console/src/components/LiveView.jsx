import { useEffect, useRef, useState } from 'react';
import './LiveView.css';
import { getLiveView } from '../api.js';
import { IconBrowser, IconRefresh } from './icons.jsx';

/**
 * Polls the session's real browser screenshot so a run is watchable.
 *
 * Polls faster while a run is active and slows right down when it isn't -
 * each frame is a real screenshot taken in the browser, so hammering it while
 * nothing is happening costs the run real time.
 */
export default function LiveView({ session, active }) {
  const [frame, setFrame] = useState(null);
  const [meta, setMeta] = useState({ url: '', title: '' });
  const [error, setError] = useState('');
  const [paused, setPaused] = useState(false);
  const timerRef = useRef(null);
  const mountedRef = useRef(true);
  // Shared across effect re-runs and StrictMode's double mount. A guard held
  // in the effect's own closure is per-instance, so two concurrent instances
  // each think they are the only one and the real screenshot rate doubles -
  // measured, not assumed.
  const inFlightRef = useRef(false);
  const lastFetchRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearTimeout(timerRef.current);
    };
  }, []);

  useEffect(() => {
    clearTimeout(timerRef.current);
    if (!session || paused) return;

    let cancelled = false;
    const interval = active ? 1500 : 6000;

    const tick = async () => {
      if (cancelled || !mountedRef.current) return;
      const since = Date.now() - lastFetchRef.current;
      // Every frame is a real screenshot taken inside the running browser, so
      // an extra poll chain steals time from the run being watched. Refuse to
      // fire early, and let whichever chain is already in flight own the slot.
      if (inFlightRef.current || since < interval - 100) {
        timerRef.current = setTimeout(tick, Math.max(200, interval - since));
        return;
      }
      inFlightRef.current = true;
      lastFetchRef.current = Date.now();
      try {
        const data = await getLiveView(session);
        if (!cancelled && mountedRef.current) {
          setFrame(data.screenshot);
          setMeta({ url: data.url, title: data.title });
          setError('');
        }
      } catch (err) {
        if (!cancelled && mountedRef.current) setError(err.message);
      } finally {
        inFlightRef.current = false;
      }
      if (cancelled || !mountedRef.current) return;
      timerRef.current = setTimeout(tick, interval);
    };

    tick();
    return () => {
      cancelled = true;
      clearTimeout(timerRef.current);
    };
  }, [session, active, paused]);

  return (
    <section className="live-view">
      <header className="live-head">
        <div className="live-head-left">
          <IconBrowser />
          <span className="live-label">Live browser</span>
          {active && <span className="dot dot-pulse" style={{ background: 'var(--ax-orange)' }} />}
        </div>
        <div className="live-head-right">
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setPaused((p) => !p)}>
            {paused ? 'Resume' : 'Pause'}
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => { setPaused(false); setFrame(null); }}
            title="Force refresh"
          >
            <IconRefresh />
          </button>
        </div>
      </header>

      <div className="live-urlbar mono" title={meta.url}>
        {meta.url || (session ? 'waiting for a page…' : 'no session selected')}
      </div>

      <div className="live-frame">
        {!session && <div className="live-placeholder">Start a run to see the browser.</div>}
        {session && error && <div className="live-placeholder live-error">{error}</div>}
        {session && !error && !frame && <div className="live-placeholder">Capturing…</div>}
        {session && !error && frame && <img src={frame} alt={meta.title || 'Live browser view'} />}
      </div>

      {meta.title && <div className="live-title">{meta.title}</div>}
    </section>
  );
}
