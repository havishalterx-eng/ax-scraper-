import { useState } from 'react';
import './TokenGate.css';
import { IconAX } from './icons.jsx';
import { getHealth, setToken } from '../api.js';

/**
 * Token entry, shown when the backend reports auth is required and the stored
 * token is missing or rejected.
 *
 * Verifies the token against a real authenticated call before storing it -
 * saving first and discovering it was wrong on the next request would leave a
 * bad value in localStorage and no obvious way to correct it.
 */
export default function TokenGate({ onAuthorised, apiBase }) {
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e) => {
    e?.preventDefault();
    const token = value.trim();
    if (!token) return;
    setBusy(true);
    setError('');
    const previous = localStorage.getItem('ax-scraper-token');
    setToken(token);
    try {
      // /health is public, so it cannot prove a token. Use a real gated
      // endpoint to check it.
      const res = await fetch(`${apiBase}/templates`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401) {
        setToken(previous ?? '');
        setError('That token was rejected. Check AX_API_TOKEN on the server.');
        return;
      }
      if (!res.ok) {
        setToken(previous ?? '');
        setError(`Backend responded ${res.status}. Is it running?`);
        return;
      }
      onAuthorised();
    } catch (err) {
      setToken(previous ?? '');
      setError(`Couldn't reach the backend (${err.message}).`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="gate">
      <form className="gate-card" onSubmit={submit}>
        <div className="gate-brand">
          <IconAX size={26} />
          <div>
            <div className="gate-name">AX Scraper</div>
            <div className="gate-sub">Console</div>
          </div>
        </div>

        <p className="gate-copy">
          This backend requires an access token. It's the value of{' '}
          <code className="mono">AX_API_TOKEN</code> on the server.
        </p>

        <input
          className="text-input gate-input"
          type="password"
          autoFocus
          placeholder="Access token"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />

        {error && <p className="gate-error">{error}</p>}

        <button type="submit" className="btn btn-primary gate-submit" disabled={busy || !value.trim()}>
          {busy ? 'Checking…' : 'Unlock'}
        </button>

        <p className="gate-note">
          Stored in this browser only, so you won't be asked again on this device.
        </p>
      </form>
    </div>
  );
}

/** Probes whether the backend wants a token and whether ours is accepted. */
export async function checkAuth(apiBase) {
  let health;
  try {
    health = await getHealth();
  } catch {
    return { reachable: false, needsToken: false };
  }
  if (!health.auth_required) return { reachable: true, needsToken: false, health };
  try {
    const stored = localStorage.getItem('ax-scraper-token') ?? '';
    const res = await fetch(`${apiBase}/templates`, {
      headers: stored ? { Authorization: `Bearer ${stored}` } : {},
    });
    return { reachable: true, needsToken: res.status === 401, health };
  } catch {
    return { reachable: false, needsToken: false };
  }
}
