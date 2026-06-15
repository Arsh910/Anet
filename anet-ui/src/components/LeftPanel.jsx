import React from 'react'
import { ChevronLeft, Plus } from 'lucide-react'
import { useStore } from '../store/useStore'
import AgentIcon from '../icons'

function AgentRow({ agent }) {
  const { selectedAgentId, setSelectedAgentId, toggleAgent } = useStore()
  const selected = selectedAgentId === agent.id
  return (
    <div
      className={`agent-row ${selected ? 'selected' : ''} ${!agent.enabled ? 'is-disabled' : ''}`}
      onClick={() => setSelectedAgentId(agent.id)}
    >
      <div className="agent-icon"><AgentIcon name={agent.icon} size={17} /></div>
      <div className="agent-meta">
        <div className="agent-name">
          {agent.name}
          {agent.isManager && <span className="tag-mgr">MGR</span>}
        </div>
        <div className="agent-desc">{agent.description}</div>
      </div>
      <span className={`dot ${agent.status}`} />
      <div
        className={`switch ${agent.enabled ? 'on' : ''}`}
        onClick={(e) => { e.stopPropagation(); toggleAgent(agent.id) }}
      />
    </div>
  )
}

export default function LeftPanel() {
  const { agents, search, setSearch, setShowAddAgent } = useStore()
  const q = search.trim().toLowerCase()
  const list = q
    ? agents.filter(a => a.name.toLowerCase().includes(q) || a.description.toLowerCase().includes(q))
    : agents

  return (
    <div className="panel left-panel">
      <div className="panel-header">
        <span className="panel-title">Agents</span>
        <span className="count-badge">{agents.length}</span>
        <div className="spacer" />
        <button className="icon-btn" title="Collapse"><ChevronLeft size={15} /></button>
      </div>

      <div className="search">
        <input
          placeholder="Search agents…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="agent-list">
        {list.map(a => <AgentRow key={a.id} agent={a} />)}
        {list.length === 0 && <div className="empty">No agents match “{search}”.</div>}
      </div>

      <div className="legend">
        <span><span className="dot active" /> Active</span>
        <span><span className="dot idle" /> Idle</span>
        <span><span className="dot disabled" /> Disabled</span>
      </div>

      <div className="panel-footer">
        <button className="btn btn-dashed" onClick={() => setShowAddAgent(true)}>
          <Plus size={15} /> Add agent
        </button>
      </div>
    </div>
  )
}
