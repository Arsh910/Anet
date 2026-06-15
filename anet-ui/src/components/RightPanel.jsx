import React from 'react'
import { Maximize2 } from 'lucide-react'
import { useStore } from '../store/useStore'
import AgentIcon from '../icons'

export default function RightPanel() {
  const { agents, selectedAgentId, toggleAgent } = useStore()
  const agent = agents.find(a => a.id === selectedAgentId)

  if (!agent) {
    return (
      <div className="panel right-panel">
        <div className="panel-header"><span className="panel-title">Inspector</span></div>
        <div className="empty">Select an agent to inspect it.</div>
      </div>
    )
  }

  const working = agent.status === 'active'

  return (
    <div className="panel right-panel">
      <div className="panel-header"><span className="panel-title">Inspector</span></div>

      <div className="inspector-body">
        {/* Header */}
        <div className="insp-head">
          <div className="insp-icon"><AgentIcon name={agent.icon} size={19} /></div>
          <div style={{ flex: 1 }}>
            <div className="row-between">
              <span className="insp-name">{agent.name}</span>
              {working && <span className="pill amber"><span className="dot active pulse" /> Working</span>}
            </div>
            <div className="insp-sub">{agent.description}</div>
          </div>
        </div>

        {/* Enable toggle */}
        <div className="enable-row row-between">
          <span>Agent enabled</span>
          <div className={`switch ${agent.enabled ? 'on' : ''}`} onClick={() => toggleAgent(agent.id)} />
        </div>

        {/* Meta */}
        <div className="meta-grid">
          <div className="meta-row"><span className="k">Provider</span><span className="v">{agent.provider || '—'}</span></div>
          <div className="meta-row"><span className="k">Model</span><span className="v mono">{agent.model}</span></div>
          <div className="meta-row"><span className="k">Max steps</span><span className="v">{agent.maxSteps ?? '—'}</span></div>
        </div>

        {/* Tools */}
        <div>
          <span className="section-label">Tools</span>
          <div className="tools-wrap">
            {(agent.tools && agent.tools.length)
              ? agent.tools.map(t => <span className="chip" key={t}>{t}</span>)
              : <span className="insp-sub">No tools assigned</span>}
          </div>
        </div>

        {/* Current task */}
        {agent.currentTask && (
          <div>
            <span className="section-label">Current task</span>
            <div className="task-block">{agent.currentTask}</div>
          </div>
        )}

        {/* Recent activity */}
        {agent.recentActivity?.length > 0 && (
          <div>
            <span className="section-label">Recent activity</span>
            <div className="activity-mini">
              {agent.recentActivity.map((a, i) => (
                <div className="ln" key={i}>
                  <span className="t">{a.time}</span>
                  <span className="kw">{a.kw}</span>
                  <span className="pa">{a.path}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="insp-footer">
        <button className="btn btn-outline" style={{ flex: 1 }} onClick={() => toggleAgent(agent.id)}>
          {agent.enabled ? 'Disable' : 'Enable'}
        </button>
        <button className="icon-btn" title="Expand"><Maximize2 size={15} /></button>
      </div>
    </div>
  )
}
