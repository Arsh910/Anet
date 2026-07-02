import React from 'react'
import { Hexagon, Plus, Settings } from 'lucide-react'
import { useStore } from '../store/useStore'
import ThemeToggle from './ThemeToggle'

export default function TopBar() {
  const { agents } = useStore()
  const model = agents.find(a => a.isManager)?.model || 'claude-sonnet-4.6'
  const working = agents.filter(a => a.status === 'active').length

  return (
    <div className="topbar">
      <div className="brand">
        <div className="brand-mark"><Hexagon size={15} strokeWidth={2.2} /></div>
        <span className="brand-name">Anet</span>
      </div>

      <div className="topbar-divider" />

      <span className="session-title">Refactor auth flow</span>
      <span className="pill muted">orchestration</span>
      <span className="badge mono">{model}</span>

      <div className="conn">
        <span className="dot success" />
        Connected
      </div>

      <div className="spacer" />

      <span className="pill amber">
        <span className="dot active pulse" />
        Working · {working} active
      </span>
      <button className="btn btn-primary"><Plus size={15} /> New session</button>
      <ThemeToggle />
      <button className="icon-btn" title="Settings"><Settings size={16} /></button>
    </div>
  )
}
