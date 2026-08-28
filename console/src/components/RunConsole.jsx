import { useCallback, useEffect, useRef, useState } from 'react';
import './RunConsole.css';
import LiveView from './LiveView.jsx';
import ActivityLog from './ActivityLog.jsx';
import ResultsPanel from './ResultsPanel.jsx';
import { IconPlay, IconStop, IconSend } from './icons.jsx';
import { cancelTask, createTask, getTask, messageTask, resumeTask } from '../api.js';

const ACTIVE = new Set(['running', 'needs_human']);

const EXAMPLES = [
  'Find the top 20 wireless headphones on Amazon India with price, rating and source URL.',
  'Collect the first page of Google Maps results for dental clinics in Hyderabad with name, rating and website.',
  'Go to https://news.ycombinator.com/newest and collect the 25 newest stories with titles and links.',
];

export default function RunConsole({ jobId, onJobStarted, session, onSessionChange }) {
  const [prompt, setPrompt] = useState('');
  const [chat, setChat] = useState('');
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');
  const [persistent, setPersistent] = useState(false);
  const [tab, setTab] = useState('activity');
  const pollRef = useRef(null);

  const active = job ? ACTIVE.has(job.status) : false;

  // Held in a ref so the recursive re-schedule doesn't reference the callback
  // while it is still being initialised - that self-capture works by accident
  // of closure timing and breaks the moment the callback is recreated. Assigned
  // in an effect rather than during render: writing a ref mid-render is a real
  // React anti-pattern (it can run twice, and it fights concurrent rendering).
  const pollFnRef = useRef(null);
  const pollImpl = async (id) => {
    try {
      const data = await getTask(id);
      setJob(data);
      setError('');
      if (ACTIVE.has(data.status)) {
        pollRef.current = setTimeout(() => pollFnRef.current?.(id), 1100);
      } else if (data.structured_result?.length) {
        setTab('results');
      }
    } catch (err) {
      // A job id from before the last backend restart that never made it to
      // disk is gone for good - say that plainly instead of leaving a raw 404
      // on screen with no explanation of what to do about it.
      setError(
        /404/.test(err.message)
          ? 'That run is no longer available — it predates the last backend restart. Start a new run.'
          : err.message
      );
    }
  };

  useEffect(() => { pollFnRef.current = pollImpl; });

  const poll = useCallback((id) => pollFnRef.current?.(id), []);

  useEffect(() => {
    clearTimeout(pollRef.current);
    setJob(null);
    if (jobId) poll(jobId);
    return () => clearTimeout(pollRef.current);
  }, [jobId, poll]);

  const start = async () => {
    const task = prompt.trim();
    if (!task) return;
    setError('');
    const sessionName = session || `run-${Math.random().toString(36).slice(2, 8)}`;
    try {
      const res = await createTask(task, sessionName, persistent);
      onSessionChange(sessionName);
      onJobStarted(res.job_id);
      setPrompt('');
    } catch (err) {
      setError(`Couldn't reach the backend (${err.message}). Is it running on :8787?`);
    }
  };

  const send = async () => {
    const text = chat.trim();
    if (!text || !job) return;
    setChat('');
    try {
      await messageTask(job.job_id, text);
      clearTimeout(pollRef.current);
      poll(job.job_id);
    } catch (err) {
      setError(err.message);
    }
  };

  const records = job?.structured_result;
  const recordCount = Array.isArray(records) ? records.length : 0;

  return (
    <div className="run-console">
      <div className="run-main">
        <LiveView session={job?.session || session} active={active} />

        <section className="run-lower">
          <div className="run-tabs">
            <button
              type="button"
              className={`run-tab ${tab === 'activity' ? 'is-active' : ''}`}
              onClick={() => setTab('activity')}
            >
              Activity {job?.log?.length ? <span className="tab-count">{job.log.length}</span> : null}
            </button>
            <button
              type="button"
              className={`run-tab ${tab === 'results' ? 'is-active' : ''}`}
              onClick={() => setTab('results')}
            >
              Results {recordCount ? <span className="tab-count">{recordCount}</span> : null}
            </button>
            {job && (
              <div className="run-status-group">
                <span className={`pill pill-${job.status === 'done' ? 'done' : job.status === 'running' ? 'running' : job.status === 'needs_human' ? 'warn' : job.status === 'error' ? 'error' : 'idle'}`}>
                  {job.status.replace('_', ' ')}
                </span>
                <span className="run-steps mono">
                  {job.mode === 'direct'
                    ? `${job.steps_used ?? 0} steps`
                    : `${job.steps_used ?? 0}/${job.max_steps ?? '—'} steps`}
                </span>
                {job.mode === 'direct' && (
                  <span className="pill pill-done" title="This run executed the template's own tool calls. No model was involved, so it cost nothing to run.">
                    no model
                  </span>
                )}
                {active && (
                  <button type="button" className="btn btn-danger btn-sm" onClick={() => cancelTask(job.job_id).then(() => poll(job.job_id))}>
                    <IconStop /> Stop
                  </button>
                )}
              </div>
            )}
          </div>

          <div className="run-tab-body">
            {tab === 'activity' ? (
              <ActivityLog log={job?.log ?? []} active={active} />
            ) : (
              <ResultsPanel records={records} result={job?.result} status={job?.status} source={job?.records_source} />
            )}
          </div>
        </section>
      </div>

      <aside className="run-side">
        <section className="run-card">
          <label className="field-label" htmlFor="run-prompt">
            {job ? 'Start another run' : 'What should the agent do?'}
          </label>
          {job && (
            <p className="run-chat-hint">
              Typing here starts a fresh run. To continue the one below, use “Talk to the agent”.
            </p>
          )}
          <textarea
            id="run-prompt"
            className="text-area"
            rows={5}
            placeholder="Describe the data you want, and where to get it."
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) start(); }}
          />
          <div className="run-options">
            <label className="run-check">
              <input type="checkbox" checked={persistent} onChange={(e) => setPersistent(e.target.checked)} />
              Signed-in session (keeps cookies on disk)
            </label>
          </div>
          <button type="button" className="btn btn-primary run-start" onClick={start} disabled={!prompt.trim()}>
            <IconPlay /> Run
          </button>
          <div className="run-examples">
            {EXAMPLES.map((ex) => (
              <button key={ex} type="button" className="run-example" onClick={() => setPrompt(ex)}>
                {ex}
              </button>
            ))}
          </div>
        </section>

        <section className="run-card run-chat">
          <span className="field-label">Talk to the agent</span>
          <p className="run-chat-hint">
            {job
              ? active
                ? 'Sent now, picked up on its next step.'
                : 'Ask about what it found, or tell it to keep going — it still remembers the page.'
              : 'Start a run first.'}
          </p>
          <div className="run-chat-row">
            <input
              className="text-input"
              placeholder={job ? 'e.g. also grab the seller name' : 'No active run'}
              value={chat}
              disabled={!job}
              onChange={(e) => setChat(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') send(); }}
            />
            <button type="button" className="btn btn-primary btn-sm" onClick={send} disabled={!job || !chat.trim()}>
              <IconSend />
            </button>
          </div>
        </section>

        {job?.status === 'needs_human' && (
          <section className="run-card run-handoff">
            <span className="field-label">Agent needs you</span>
            <p className="run-handoff-reason">{job.human_reason || 'The agent asked for a human to take over this step.'}</p>
            <p className="run-chat-hint">Do it in the real browser window, then continue.</p>
            <button type="button" className="btn btn-primary" onClick={() => resumeTask(job.job_id).then(() => poll(job.job_id))}>
              I've handled it — continue
            </button>
          </section>
        )}

        {(error || job?.error) && (
          <section className="run-card run-error">
            <span className="field-label">Problem</span>
            <p>{error || job.error}</p>
          </section>
        )}
      </aside>
    </div>
  );
}
