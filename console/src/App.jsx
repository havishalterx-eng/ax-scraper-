import { useEffect, useState } from 'react';
import './App.css';
import Sidebar from './components/Sidebar.jsx';
import RunConsole from './components/RunConsole.jsx';
import AgentsView from './components/AgentsView.jsx';
import TemplatesView from './components/TemplatesView.jsx';
import HomeView from './components/HomeView.jsx';
import { ProxiesView, DeliveryView } from './components/InfraViews.jsx';
import { HistoryView, SessionsView } from './components/SimpleViews.jsx';
import TokenGate, { checkAuth } from './components/TokenGate.jsx';
import { API_BASE, UnauthorizedError, getHealth, listTasks, setHeadless } from './api.js';

export default function App() {
  const [view, setView] = useState('home');
  const [jobId, setJobId] = useState(null);
  const [session, setSession] = useState('');
  const [tasks, setTasks] = useState([]);
  const [health, setHealth] = useState(null);
  // null = still probing; the gate must not flash before we know.
  const [needsToken, setNeedsToken] = useState(null);

  useEffect(() => {
    let stop = false;
    checkAuth(API_BASE).then((r) => { if (!stop) setNeedsToken(r.needsToken); });
    return () => { stop = true; };
  }, []);

  useEffect(() => {
    if (needsToken !== false) return;
    let stop = false;
    const tick = async () => {
      try {
        const h = await getHealth();
        if (!stop) setHealth(h);
      } catch {
        if (!stop) setHealth(null);
      }
      try {
        const { tasks: list } = await listTasks();
        if (!stop) setTasks(list);
      } catch (err) {
        // A token revoked or rotated server-side shows up here first; send the
        // user back to the gate rather than leaving a silently dead console.
        if (err instanceof UnauthorizedError && !stop) setNeedsToken(true);
      }
    };
    tick();
    const t = setInterval(tick, 4000);
    return () => { stop = true; clearInterval(t); };
  }, [needsToken]);

  const openJob = (id) => { setJobId(id); setView('run'); };

  if (needsToken === null) return <div className="app" />;
  if (needsToken) {
    return <TokenGate apiBase={API_BASE} onAuthorised={() => setNeedsToken(false)} />;
  }

  return (
    <div className="app">
      <Sidebar
        view={view}
        onView={setView}
        tasks={tasks}
        activeJobId={jobId}
        onSelectJob={openJob}
        health={health}
        onToggleHeadless={async () => {
          const next = !health?.headless;
          setHealth((h) => (h ? { ...h, headless: next } : h));
          try { await setHeadless(next); } catch { /* next poll corrects it */ }
        }}
        onNewRun={() => {
          // Clear the session too, so a new run gets its own fresh browser
          // rather than inheriting the last run's page.
          setJobId(null);
          setSession('');
          setView('home');
        }}
      />

      <main className="app-main">
        {view === 'home' && (
          <HomeView onJobStarted={setJobId} onView={setView} tasks={tasks} health={health} />
        )}
        {view === 'templates' && <TemplatesView onJobStarted={setJobId} onView={setView} />}
        {view === 'agents' && <AgentsView onJobStarted={setJobId} onView={setView} />}
        {view === 'run' && (
          <RunConsole
            jobId={jobId}
            onJobStarted={setJobId}
            session={session}
            onSessionChange={setSession}
          />
        )}
        {view === 'history' && <HistoryView onSelectJob={setJobId} onView={setView} />}
        {view === 'sessions' && <SessionsView />}
        {view === 'proxies' && <ProxiesView />}
        {view === 'integrations' && <DeliveryView />}
      </main>
    </div>
  );
}
