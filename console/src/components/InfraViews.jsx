import { useEffect, useState } from 'react';
import './InfraViews.css';
import { IconRefresh } from './icons.jsx';
import { getInfrastructure } from '../api.js';

/**
 * Proxies and Delivery report the runtime's real configuration.
 *
 * Deliberately shows "not configured" as a plain statement plus the exact env
 * var that would change it, rather than a disabled control that implies the
 * feature is one click away. Nothing here is a placeholder for something that
 * doesn't exist.
 */
function useInfra() {
  const [infra, setInfra] = useState(null);
  const [error, setError] = useState('');
  const load = async () => {
    try {
      setInfra(await getInfrastructure());
      setError('');
    } catch (e) { setError(`Backend unreachable (${e.message}).`); }
  };
  useEffect(() => { load(); }, []);
  return { infra, error, reload: load };
}

function Row({ label, value, tone }) {
  return (
    <div className="infra-row">
      <span className="infra-row-label">{label}</span>
      <span className={`infra-row-value ${tone ? `is-${tone}` : ''}`}>{value}</span>
    </div>
  );
}

export function ProxiesView() {
  const { infra, error, reload } = useInfra();
  const proxy = infra?.proxy;
  const stealth = infra?.stealth;
  const captcha = infra?.captcha;

  return (
    <div className="infra-view">
      <header className="infra-head">
        <div>
          <h1 className="infra-h1">Proxies &amp; stealth</h1>
          <p className="infra-hint">How runs reach the internet, and what identity they present.</p>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={reload}><IconRefresh /> Refresh</button>
      </header>

      {error && <div className="infra-error">{error}</div>}

      <div className="infra-cards">
        <section className="infra-card">
          <div className="infra-card-head">
            <h2 className="infra-card-title">Exit routing</h2>
            <span className={`pill ${proxy?.configured ? 'pill-done' : 'pill-idle'}`}>
              {proxy?.configured ? 'proxy active' : 'direct'}
            </span>
          </div>
          {proxy && (
            <>
              <Row label="Proxy server" value={proxy.server ?? 'none'} tone={proxy.configured ? '' : 'muted'} />
              <Row label="Credentials" value={proxy.has_credentials ? 'set' : 'none'} />
              <Row label="IP per session" value={proxy.rotating ? 'rotating' : 'shared'} />
              <Row label="Country targeting" value="not available" tone="warn" />
              <p className="infra-note">{proxy.note}</p>
              <p className="infra-note infra-note-dim">
                Set <code className="mono">PROXY_SERVER</code>, <code className="mono">PROXY_USERNAME</code>,{' '}
                <code className="mono">PROXY_PASSWORD</code> on the backend to route through a proxy, and{' '}
                <code className="mono">PROXY_ROTATE=1</code> for a distinct IP per session.
              </p>
              <p className="infra-note infra-note-dim">
                Picking an exit <strong>country</strong> is not wired up. The one proxy zone set up is a small
                fixed-IP ISP pool with no country selection — that needs a residential product with its own
                verification, so no UI here pretends otherwise.
              </p>
            </>
          )}
        </section>

        <section className="infra-card">
          <div className="infra-card-head">
            <h2 className="infra-card-title">Browser identity</h2>
            <span className="pill pill-done">active</span>
          </div>
          {stealth && (
            <>
              <Row label="Engine" value={stealth.engine} />
              <Row label="Block trackers" value={stealth.block_trackers ? 'on' : 'off'} />
              <Row label="Block images" value={stealth.block_media ? 'on' : 'off'} />
              <p className="infra-note">
                Runs use patchright rather than stock Playwright — it patches the automation tells anti-bot
                scripts check first (<code className="mono">navigator.webdriver</code> reports false, verified).
              </p>
              <p className="infra-note infra-note-dim">
                Images load by default so live screenshots aren't full of holes. Set{' '}
                <code className="mono">BLOCK_MEDIA=1</code> to trade that back for bandwidth.
              </p>
            </>
          )}
        </section>

        <section className="infra-card">
          <div className="infra-card-head">
            <h2 className="infra-card-title">CAPTCHA</h2>
            <span className={`pill ${captcha?.configured ? 'pill-done' : 'pill-idle'}`}>
              {captcha?.configured ? '2captcha active' : 'not configured'}
            </span>
          </div>
          {captcha && <p className="infra-note">{captcha.note}</p>}
          <p className="infra-note infra-note-dim">
            {captcha?.configured
              ? 'A run that hits a CAPTCHA solves it automatically and keeps going — nothing to click.'
              : "When a run hits a challenge it calls for a human instead — you'll get a “needs you” prompt in the console, and you clear it in the real browser window. Turn the window on from the sidebar footer toggle before doing that, or there's nothing to click."}
          </p>
        </section>
      </div>
    </div>
  );
}

export function DeliveryView() {
  const { error, reload } = useInfra();

  return (
    <div className="infra-view">
      <header className="infra-head">
        <div>
          <h1 className="infra-h1">Delivery</h1>
          <p className="infra-hint">Where finished results can go.</p>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={reload}><IconRefresh /> Refresh</button>
      </header>

      {error && <div className="infra-error">{error}</div>}

      <div className="infra-cards">
        <section className="infra-card">
          <div className="infra-card-head">
            <h2 className="infra-card-title">Download</h2>
            <span className="pill pill-done">working</span>
          </div>
          <p className="infra-note">
            Every finished run with structured records offers CSV and JSON straight from the Results tab. The
            rows come from what the browser actually read, not from the model retyping them.
          </p>
        </section>

        <section className="infra-card">
          <div className="infra-card-head">
            <h2 className="infra-card-title">Webhook</h2>
            <span className="pill pill-done">working</span>
          </div>
          <p className="infra-note">
            Give an agent a webhook URL and every completed run POSTs its result there — status, prose summary
            and the structured records.
          </p>
          <p className="infra-note infra-note-dim">
            Set it per agent via the API: <code className="mono">PATCH /agents/&lt;id&gt; {'{'}"webhook_url": "…"{'}'}</code>
          </p>
        </section>

        <section className="infra-card">
          <div className="infra-card-head">
            <h2 className="infra-card-title">Make · n8n · Zapier · cloud storage</h2>
            <span className="pill pill-idle">not built</span>
          </div>
          <p className="infra-note">
            None of these are connected. Each needs its own vendor app and OAuth credentials, so there's
            nothing behind them yet.
          </p>
          <p className="infra-note infra-note-dim">
            The plain webhook above already reaches all three — Make, n8n and Zapier can all receive one. A
            first-class integration would only add nicer setup, not new capability.
          </p>
        </section>

        <section className="infra-card">
          <div className="infra-card-head">
            <h2 className="infra-card-title">HTTP API</h2>
            <span className="pill pill-done">working</span>
          </div>
          <p className="infra-note">
            Everything this console does is an HTTP call you can make yourself — create runs, poll them, manage
            agents, read results.
          </p>
          <p className="infra-note infra-note-dim mono">POST /tasks · GET /tasks/&lt;id&gt; · POST /agents · GET /templates</p>
          <p className="infra-note infra-note-dim">
            No auth on any of it. Fine while it's local-only; it needs auth before it's reachable from anywhere else.
          </p>
        </section>
      </div>
    </div>
  );
}
