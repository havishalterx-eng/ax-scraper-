/**
 * Where the API lives.
 *
 * In production the backend serves this bundle itself, so the API shares the
 * page's origin and a relative base is both correct and CORS-free wherever it
 * is deployed - hardcoding a host would break the moment it left localhost.
 * The Vite dev server runs on its own port, so that case falls back to the
 * local backend. VITE_API_BASE overrides both.
 */
const DEV_PORTS = new Set(['5182', '5173']);

export const API_BASE =
  import.meta.env.VITE_API_BASE ??
  (DEV_PORTS.has(window.location.port) ? 'http://localhost:8787' : '');

/**
 * Shared API token.
 *
 * Kept in localStorage so a phone doesn't have to be re-authorised every time
 * the tab is reopened. This is a single-operator secret, not a session: there
 * is no per-user identity behind it and it should not be treated as one.
 */
const TOKEN_KEY = 'ax-scraper-token';

export const getToken = () => localStorage.getItem(TOKEN_KEY) ?? '';
export const setToken = (value) => {
  if (value) localStorage.setItem(TOKEN_KEY, value);
  else localStorage.removeItem(TOKEN_KEY);
};

/** Thrown on 401 so the UI can show the token prompt instead of a raw error. */
export class UnauthorizedError extends Error {
  constructor(message) {
    super(message || 'Unauthorized');
    this.name = 'UnauthorizedError';
  }
}

function withAuth(options = {}) {
  const token = getToken();
  if (!token) return options;
  return {
    ...options,
    headers: { ...(options.headers ?? {}), Authorization: `Bearer ${token}` },
  };
}

async function req(path, options) {
  const res = await fetch(`${API_BASE}${path}`, withAuth(options));
  const body = await res.json().catch(() => ({}));
  if (res.status === 401) throw new UnauthorizedError(body.error);
  if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
  return body;
}

const jsonPost = (path, payload) =>
  req(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload ?? {}),
  });

export const getHealth = () => req('/health');
export const setHeadless = (headless) => jsonPost('/config', { headless });
export const listTools = () => req('/tools');
export const callTool = (name, args) => jsonPost(`/tools/${name}`, args);

export const listSessions = () => req('/sessions');
export const getLiveView = (session) => req(`/sessions/${encodeURIComponent(session)}/live`);

export const listTasks = () => req('/tasks');
export const getTask = (jobId) => req(`/tasks/${jobId}`);
export const createTask = (task, session, persistent = false) =>
  jsonPost('/tasks', { task, session, persistent });
export const messageTask = (jobId, text) => jsonPost(`/tasks/${jobId}/message`, { text });
export const cancelTask = (jobId) => jsonPost(`/tasks/${jobId}/cancel`);
export const resumeTask = (jobId) => jsonPost(`/tasks/${jobId}/resume`);

export const listAgents = () => req('/agents');
export const getAgent = (id) => req(`/agents/${id}`);
export const createAgent = (payload) => jsonPost('/agents', payload);
export const runAgent = (id) => jsonPost(`/agents/${id}/run`);
export const deleteAgent = (id) => req(`/agents/${id}`, { method: 'DELETE' });
export const updateAgent = (id, payload) =>
  req(`/agents/${id}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
export const setSchedule = (id, payload) =>
  req(`/agents/${id}/schedule`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
export const toggleSchedule = (id, active) =>
  req(`/agents/${id}/schedule`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ active }),
  });

export const listTemplates = () => req('/templates');
export const startTemplate = (id, payload) => jsonPost(`/templates/${id}/use`, payload);
export const getInfrastructure = () => req('/infrastructure');
