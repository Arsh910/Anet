import React, { useState } from 'react'
import { useStore } from '../store/useStore'
import AddAgentModal from './AddAgentModal'

function stripSuffix(name) {
  return name.replace(/_agent$/i, '')
}

function statusDotStyle(status) {
  const base = {
    width: 5,
    height: 5,
    borderRadius: '50%',
    flexShrink: 0,
  }
  switch (status) {
    case 'running':
      return { ...base, background: '#639922', animation: 'pulse-fast 1s infinite' }
    case 'async':
      return { ...base, background: '#BA7517', animation: 'pulse-slow 2s infinite' }
    case 'idle':
      return { ...base, background: 'rgba(255,255,255,0.25)' }
    case 'disabled':
      return { ...base, background: 'rgba(255,255,255,0.08)' }
    case 'active':
    case 'manager':
      return { ...base, background: '#7F77DD', animation: 'pulse-fast 1.5s infinite' }
    default:
      return { ...base, background: 'rgba(255,255,255,0.25)' }
  }
}

function StatusBadge({ status }) {
  const styles = {
    running: { bg: 'rgba(99,153,34,0.15)', text: '#639922' },
    async:   { bg: 'rgba(186,117,23,0.15)', text: '#BA7517' },
    idle:    { bg: 'rgba(255,255,255,0.06)', text: 'rgba(255,255,255,0.3)' },
    disabled:{ bg: 'rgba(255,255,255,0.04)', text: 'rgba(255,255,255,0.15)' },
    active:  { bg: 'rgba(127,119,221,0.15)', text: '#7F77DD' },
    manager: { bg: 'rgba(127,119,221,0.15)', text: '#7F77DD' },
  }
  const s = styles[status] || styles.idle
  return (
    <span style={{
      fontSize: 9,
      background: s.bg,
      color: s.text,
      borderRadius: 3,
      padding: '2px 5px',
      letterSpacing: 0.3,
    }}>
      {status}
    </span>
  )
}

function MetricCard({ label, value, color, dim }) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.03)',
      border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 4,
      padding: '6px 8px',
    }}>
      <div style={{
        fontSize: 16,
        fontWeight: 500,
        color: dim ? 'rgba(255,255,255,0.15)' : (color || '#e2e4e8'),
        lineHeight: 1.2,
      }}>
        {value}
      </div>
      <div style={{ fontSize: 9, color: 'rgba(255,255,255,0.25)', marginTop: 2, textTransform: 'lowercase' }}>
        {label}
      </div>
    </div>
  )
}

export default function LeftPanel() {
  const agents = useStore(s => s.agents)
  const selectedAgent = useStore(s => s.selectedAgent)
  const setSelectedAgent = useStore(s => s.setSelectedAgent)
  const tasks = useStore(s => s.tasks)
  const [showModal, setShowModal] = useState(false)

  const activeCount = agents.filter(a => a.status !== 'disabled').length
  const totalCount = agents.length

  const tasksToday = agents.reduce((sum, a) => sum + (a.tasks_today || 0), 0)
  const failedCount = agents.reduce((sum, a) => sum + (a.failed_today || 0), 0)
  const asyncCount = agents.filter(a => a.status === 'async').length

  // Avg response time
  let avgResp = '—'
  const respTimes = agents.map(a => a.avg_response_ms).filter(v => v != null && v > 0)
  if (respTimes.length > 0) {
    const avg = respTimes.reduce((a, b) => a + b, 0) / respTimes.length
    avgResp = avg < 1000 ? `${Math.round(avg)}ms` : `${(avg / 1000).toFixed(1)}s`
  }

  return (
    <div style={{
      width: 200,
      minWidth: 200,
      height: '100%',
      background: '#0d0f12',
      borderRight: '1px solid rgba(255,255,255,0.06)',
      overflowY: 'auto',
      padding: 12,
      display: 'flex',
      flexDirection: 'column',
      gap: 0,
    }}>
      {/* TELEMETRY section */}
      <div style={{ fontSize: 9, color: 'rgba(255,255,255,0.2)', textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 8 }}>
        Telemetry
      </div>

      {/* Active agents card */}
      <div style={{
        background: 'rgba(255,255,255,0.04)',
        border: '1px solid rgba(255,255,255,0.07)',
        borderRadius: 6,
        padding: 10,
      }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 3 }}>
          <span style={{ fontSize: 24, fontWeight: 500, color: '#e2e4e8', lineHeight: 1 }}>{activeCount}</span>
          <span style={{ fontSize: 13, color: 'rgba(255,255,255,0.3)' }}>/ {totalCount}</span>
        </div>
        <div style={{ fontSize: 9, color: 'rgba(255,255,255,0.2)', marginTop: 4, textTransform: 'lowercase' }}>
          active agents
        </div>
      </div>

      {/* 2x2 metric grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginTop: 8 }}>
        <MetricCard label="tasks today" value={tasksToday} color="#639922" dim={tasksToday === 0} />
        <MetricCard label="failed" value={failedCount} color="#E24B4A" dim={failedCount === 0} />
        <MetricCard label="avg resp" value={avgResp} color="#378ADD" />
        <MetricCard label="offloaded" value={asyncCount} color="#BA7517" dim={asyncCount === 0} />
      </div>

      {/* AGENTS section */}
      <div style={{ fontSize: 9, color: 'rgba(255,255,255,0.2)', textTransform: 'uppercase', letterSpacing: 0.8, marginTop: 16, marginBottom: 8 }}>
        Agents
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 1, flex: 1 }}>
        {agents.length === 0 && (
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.2)', padding: '8px 0', textAlign: 'center' }}>
            No agents registered
          </div>
        )}
        {agents.map(agent => {
          const isSelected = selectedAgent && selectedAgent.name === agent.name
          const isDisabled = agent.status === 'disabled'
          return (
            <AgentRow
              key={agent.name}
              agent={agent}
              isSelected={isSelected}
              isDisabled={isDisabled}
              onClick={() => setSelectedAgent(agent)}
            />
          )
        })}
      </div>

      {/* Add agent button */}
      <button
        onClick={() => setShowModal(true)}
        style={{
          width: '100%',
          marginTop: 10,
          padding: '7px 0',
          background: 'transparent',
          border: '1px dashed rgba(255,255,255,0.1)',
          borderRadius: 4,
          color: 'rgba(255,255,255,0.3)',
          fontSize: 10,
          cursor: 'pointer',
          fontFamily: 'inherit',
          transition: 'border-color 0.15s, color 0.15s',
        }}
        onMouseEnter={e => {
          e.currentTarget.style.borderColor = 'rgba(55,138,221,0.4)'
          e.currentTarget.style.color = '#378ADD'
        }}
        onMouseLeave={e => {
          e.currentTarget.style.borderColor = 'rgba(255,255,255,0.1)'
          e.currentTarget.style.color = 'rgba(255,255,255,0.3)'
        }}
      >
        + Add agent
      </button>

      {showModal && <AddAgentModal onClose={() => setShowModal(false)} />}
    </div>
  )
}

function AgentRow({ agent, isSelected, isDisabled, onClick }) {
  const [hovered, setHovered] = useState(false)

  const displayName = stripSuffix(agent.name)

  let bg = 'transparent'
  if (isSelected) bg = 'rgba(55,138,221,0.08)'
  else if (hovered) bg = 'rgba(255,255,255,0.04)'

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        padding: '6px 8px',
        borderRadius: 4,
        cursor: 'pointer',
        background: bg,
        borderLeft: isSelected ? '2px solid #378ADD' : '2px solid transparent',
        opacity: isDisabled ? 0.4 : 1,
        display: 'flex',
        alignItems: 'center',
        gap: 7,
        transition: 'background 0.1s',
      }}
    >
      <div style={statusDotStyle(agent.status)} />
      <span style={{
        flex: 1,
        fontSize: 11,
        color: isDisabled ? 'rgba(255,255,255,0.4)' : 'rgba(255,255,255,0.85)',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}>
        {displayName}
      </span>
      <StatusBadge status={agent.status} />
    </div>
  )
}
