import { create } from 'zustand'

const API = 'http://localhost:8000'
const WS_URL = 'ws://localhost:8000/ws/log'

export const useStore = create((set, get) => ({
  // Agent data
  agents: [],
  selectedAgent: null,
  setSelectedAgent: (agent) => set({ selectedAgent: agent }),

  // Log
  logs: [],
  addLog: (entry) => set(state => ({
    logs: [...state.logs.slice(-99), entry]
  })),

  // Tasks
  tasks: [],

  // Fetch agents every 3 seconds
  fetchAgents: async () => {
    try {
      const r = await fetch(`${API}/agents`)
      const agents = await r.json()
      set({ agents })
      // Update selectedAgent if it changed
      const sel = get().selectedAgent
      if (sel) {
        const updated = agents.find(a => a.name === sel.name)
        if (updated) set({ selectedAgent: updated })
      }
    } catch {}
  },

  fetchTasks: async () => {
    try {
      const r = await fetch(`${API}/tasks`)
      set({ tasks: await r.json() })
    } catch {}
  },

  toggleAgent: async (name) => {
    try {
      const r = await fetch(`${API}/agents/${name}/toggle`, { method: 'PATCH' })
      const updated = await r.json()
      set(state => ({
        agents: state.agents.map(a => a.name === name ? { ...a, status: updated.status, enabled: updated.status !== 'disabled' } : a),
      }))
    } catch {}
  },

  registerAgent: async (path) => {
    const r = await fetch(`${API}/agents/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    })
    if (!r.ok) {
      const e = await r.json()
      throw new Error(e.detail || 'Registration failed')
    }
    const agent = await r.json()
    await get().fetchAgents()
    return agent
  },

  scanPath: async (path) => {
    const r = await fetch(`${API}/agents/scan?path=${encodeURIComponent(path)}`)
    if (!r.ok) throw new Error('Path not found')
    return r.json()
  },

  // WebSocket setup
  wsRef: null,
  connectWS: () => {
    const ws = new WebSocket(WS_URL)
    ws.onmessage = (e) => {
      try {
        const entry = JSON.parse(e.data)
        get().addLog(entry)
      } catch {}
    }
    ws.onclose = () => {
      setTimeout(() => get().connectWS(), 3000)
    }
    set({ wsRef: ws })
  },
}))
