import { useEffect, useState } from 'react';
import './App.css';
import Sidebar from './components/Sidebar.jsx';
import RunConsole from './components/RunConsole.jsx';
import AgentsView from './components/AgentsView.jsx';
import TemplatesView from './components/TemplatesView.jsx';
import HomeView from './components/HomeView.jsx';
import { ProxiesView, DeliveryView } from './components/InfraViews.jsx';
import { HistoryView, SessionsView } from './components/SimpleViews.jsx';
import { getHealth, listTasks, setHeadless } from './api.js';

export default function App() {
  const [view, setView] = useState('home');
  const [jobId, setJobId] = useState(null);
  const [session, setSession] = useState('');
  const [tasks, setTasks] = useState([]);
  const [health, setHealth] = useState(null);

  useEffect(() => {
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
      } catch { /* the sidebar footer already reports the outage */ }
    };
    tick();
    const t = setInterval(tick, 4000);
    return () => { stop = true; clearInterval(t); };
  }, []);

  const openJob = (id) => { setJobId(id); setView('run'); };

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
