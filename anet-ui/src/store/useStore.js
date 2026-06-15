import { create } from 'zustand'

const API = 'http://localhost:8000'
const WS_URL = 'ws://localhost:8000/ws/log'

/* Mock data — mirrors design.js so the UI renders fully without a backend.
   Real data wiring happens later; fetch* fall back to this silently. */
const MOCK_AGENTS = [
  {
    id: 'manager', name: 'Manager', role: 'MGR', description: 'Plans & routes tasks',
    icon: 'org-chart', model: 'claude-sonnet-4.6', provider: 'Anthropic',
    status: 'active', enabled: true, isManager: true, maxSteps: 40, tools: [],
  },
  {
    id: 'code', name: 'Code', description: 'Writes & edits code',
    icon: 'code-brackets', model: 'claude-sonnet-4.6', provider: 'Anthropic',
    status: 'active', enabled: true, maxSteps: 24,
    tools: ['read_file', 'write_file', 'run_tests', 'git'],
    currentTask: 'Refactoring auth middleware to support token rotation',
    recentActivity: [
      { time: '12:05:46', kw: 'write_file', path: 'src/auth/token.ts' },
      { time: '12:05:40', kw: 'run_tests', path: 'auth/*.test.ts' },
      { time: '12:05:31', kw: 'read_file', path: 'src/auth/middleware.ts' },
    ],
  },
  {
    id: 'research', name: 'Research', description: 'Searches & synthesizes',
    icon: 'megaphone', model: 'claude-haiku-4.5', provider: 'Anthropic',
    status: 'active', enabled: true, maxSteps: 10, tools: ['web_search', 'download_file'],
  },
  {
    id: 'file', name: 'File', description: 'Reads & manages files',
    icon: 'folder', model: 'fs-tools', provider: 'local',
    status: 'idle', enabled: true, maxSteps: 25, tools: ['file_tool', 'conflict_tool'],
  },
  {
    id: 'computer', name: 'Computer', description: 'Controls the desktop',
    icon: 'monitor', model: 'cua-1', provider: 'local',
    status: 'idle', enabled: true, maxSteps: 20, tools: ['open_app'],
  },
  {
    id: 'checker', name: 'Checker', description: 'Verifies outputs',
    icon: 'shield-check', model: 'claude-haiku-4.5', provider: 'Anthropic',
    status: 'active', enabled: true, maxSteps: 8, tools: ['checker'],
  },
  {
    id: 'design-critic', name: 'Design Critic', description: 'Custom · reviews UI',
    icon: 'sparkles', model: 'claude-sonnet-4.6', provider: 'Anthropic',
    status: 'disabled', enabled: false, maxSteps: 12, tools: [],
  },
]

const MOCK_LOGS = [
  { time: '12:05:30', level: 'TOOL', agent: 'checker', message: 'lint', chip: 'eslint src/auth' },
  { time: '12:05:33', level: 'WARN', agent: 'checker', message: 'Unused import ·', chip: 'token.ts:3' },
  { time: '12:05:34', level: 'TOOL', agent: 'research', message: 'fetch', chip: 'ietf.org/rfc9700' },
  { time: '12:05:37', level: 'INFO', agent: 'manager', message: 'Synthesizing research findings' },
  { time: '12:05:40', level: 'TOOL', agent: 'code', message: 'run_tests', chip: 'auth/*.test.ts' },
  { time: '12:05:41', level: 'ERR', agent: 'checker', message: 'Assertion failed ·', chip: 'rotate.test.ts:42' },
  { time: '12:05:43', level: 'PLAN', agent: 'manager', message: 'Re-delegating fix →', chip: 'code-agent' },
  { time: '12:05:46', level: 'TOOL', agent: 'code', message: 'write_file', chip: 'src/auth/token.ts' },
]

const MOCK_EDGES = [
  { from: 'manager', to: 'code', state: 'active' },
  { from: 'manager', to: 'research', state: 'active' },
  { from: 'manager', to: 'checker', state: 'active' },
  { from: 'manager', to: 'file', state: 'idle' },
  { from: 'manager', to: 'computer', state: 'idle' },
  { from: 'manager', to: 'design-critic', state: 'disabled' },
]

export const useStore = create((set, get) => ({
  agents: MOCK_AGENTS,
  edges: MOCK_EDGES,
  logs: MOCK_LOGS,
  tasks: [],

  selectedAgentId: 'code',
  setSelectedAgentId: (id) => set({ selectedAgentId: id }),

  // UI state
  theme: 'dark',
  toggleTheme: () => set(s => {
    const theme = s.theme === 'dark' ? 'light' : 'dark'
    document.documentElement.setAttribute('data-theme', theme)
    return { theme }
  }),
  leftCollapsed: false,
  rightCollapsed: false,
  activityCollapsed: false,
  toggleLeft: () => set(s => ({ leftCollapsed: !s.leftCollapsed })),
  toggleRight: () => set(s => ({ rightCollapsed: !s.rightCollapsed })),
  toggleActivity: () => set(s => ({ activityCollapsed: !s.activityCollapsed })),

  search: '',
  setSearch: (search) => set({ search }),
  logFilter: 'All',
  setLogFilter: (logFilter) => set({ logFilter }),
  autoScroll: true,
  toggleAutoScroll: () => set(s => ({ autoScroll: !s.autoScroll })),
  showAddAgent: false,
  setShowAddAgent: (showAddAgent) => set({ showAddAgent }),

  addLog: (entry) => set(state => ({ logs: [...state.logs.slice(-99), entry] })),
  clearLogs: () => set({ logs: [] }),

  // Local toggle (optimistic). Real PATCH wired in later.
  toggleAgent: (id) => set(state => ({
    agents: state.agents.map(a =>
      a.id === id
        ? { ...a, enabled: !a.enabled, status: a.enabled ? 'disabled' : 'idle' }
        : a
    ),
  })),

  // ── Backend hooks (no-op until the server is wired; mock data persists) ────
  fetchAgents: async () => {
    try {
      const r = await fetch(`${API}/agents`)
      const agents = await r.json()
      if (Array.isArray(agents) && agents.length) set({ agents })
    } catch {}
  },
  fetchTasks: async () => {
    try {
      const r = await fetch(`${API}/tasks`)
      set({ tasks: await r.json() })
    } catch {}
  },
  registerAgent: async (path) => {
    const r = await fetch(`${API}/agents/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    })
    if (!r.ok) { const e = await r.json(); throw new Error(e.detail || 'Registration failed') }
    const agent = await r.json()
    await get().fetchAgents()
    return agent
  },
  scanPath: async (path) => {
    const r = await fetch(`${API}/agents/scan?path=${encodeURIComponent(path)}`)
    if (!r.ok) throw new Error('Path not found')
    return r.json()
  },

  wsRef: null,
  connectWS: () => {
    try {
      const ws = new WebSocket(WS_URL)
      ws.onmessage = (e) => { try { get().addLog(JSON.parse(e.data)) } catch {} }
      ws.onclose = () => { setTimeout(() => get().connectWS(), 5000) }
      ws.onerror = () => { try { ws.close() } catch {} }
      set({ wsRef: ws })
    } catch {}
  },
}))
