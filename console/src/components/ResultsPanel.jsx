import { useMemo, useState } from 'react';
import './ResultsPanel.css';
import { IconDownload } from './icons.jsx';

function toCsv(records) {
  if (!records.length) return '';
  const keys = [...new Set(records.flatMap((r) => Object.keys(r)))];
  const esc = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  return [keys.join(','), ...records.map((r) => keys.map((k) => esc(r[k])).join(','))].join('\n');
}

function download(filename, content, mime) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function ResultsPanel({ records, result, status, source }) {
  const [copied, setCopied] = useState(false);

  const keys = useMemo(() => {
    if (!records?.length) return [];
    const seen = [];
    records.forEach((r) => Object.keys(r).forEach((k) => { if (!seen.includes(k)) seen.push(k); }));
    // Long free-text fields make the table unreadable; they stay in the export.
    return seen.filter((k) => k !== 'text' && k !== 'image').slice(0, 7);
  }, [records]);

  if (!records?.length) {
    return (
      <div className="results-panel">
        <p className="empty-note">
          {status === 'done'
            ? 'This run finished without returning a structured record list. The full answer is in the activity log.'
            : 'Structured records show up here once a run finishes.'}
        </p>
        {status === 'done' && result && <pre className="results-prose mono">{result.slice(0, 1200)}</pre>}
      </div>
    );
  }

  return (
    <div className="results-panel">
      <div className="results-bar">
        <span className="results-count">
          <strong>{records.length}</strong> record{records.length === 1 ? '' : 's'}
          {source === 'extracted' && (
            <span className="results-source" title="Captured directly from the page, not retyped by the model">
              read from page
            </span>
          )}
          {source === 'model' && (
            <span className="results-source results-source-model" title="Assembled by the model rather than captured by the extractor - worth spot-checking">
              model-assembled
            </span>
          )}
        </span>
        <div className="results-actions">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => { download('ax-results.csv', toCsv(records), 'text/csv'); }}
          >
            <IconDownload /> CSV
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => { download('ax-results.json', JSON.stringify(records, null, 2), 'application/json'); }}
          >
            <IconDownload /> JSON
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(JSON.stringify(records, null, 2));
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              } catch { /* clipboard blocked - the download buttons still work */ }
            }}
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      </div>

      <div className="results-table-wrap">
        <table className="results-table">
          <thead>
            <tr>
              <th className="results-idx">#</th>
              {keys.map((k) => <th key={k}>{k.replace(/_/g, ' ')}</th>)}
            </tr>
          </thead>
          <tbody>
            {records.map((r, i) => (
              <tr key={i}>
                <td className="results-idx mono">{i + 1}</td>
                {keys.map((k) => (
                  <td key={k} title={String(r[k] ?? '')}>
                    {k === 'url' && r[k] ? (
                      <a href={r[k]} target="_blank" rel="noreferrer noopener" className="results-link">open</a>
                    ) : (
                      String(r[k] ?? '—')
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
