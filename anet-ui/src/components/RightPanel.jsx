import React, { useState } from 'react'
import { useStore } from '../store/useStore'

function SectionLabel({ children }) {
  return (
    <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.2)', textTransform: 'uppercase', letterSpacing: 0.8 }}>
      {children}
    </span>
  )
}

function InfoRow({ label, value, valueColor }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8, marginBottom: 4 }}>
      <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.3)', flexShrink: 0 }}>{label}</span>
      <span style={{ fontSize: 9, color: valueColor || 'rgba(255,255,255,0.7)', textAlign: 'right', wordBreak: 'break-all' }}>
        {value || '—'}
      </span>
    </div>
  )
}

function CollapsibleBlock({ title, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div style={{
      background: 'rgba(255,255,255,0.03)',
      border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 6,
      marginBottom: 8,
      overflow: 'hidden',
    }}>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '7px 10px',
          cursor: 'pointer',
          userSelect: 'none',
        }}
        onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.02)'}
        onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
      >
        <SectionLabel>{title}</SectionLabel>
        <svg
          width="10"
          height="10"
          viewBox="0 0 10 10"
          style={{
            transform: open ? 'rotate(180deg)' : 'rotate(0deg)',
            transition: 'transform 0.15s',
            color: 'rgba(255,255,255,0.2)',
          }}
        >
          <path d="M2 3.5 L5 6.5 L8 3.5" stroke="currentColor" strokeWidth="1.2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
      {open && (
        <div style={{ padding: '4px 10px 10px 10px' }}>
          {children}
        </div>
      )}
    </div>
  )
}

function ToggleSwitch({ enabled, onToggle }) {
  return (
    <div
      onClick={onToggle}
      style={{
        width: 28,
        height: 16,
        borderRadius: 8,
        background: enabled ? 'rgba(55,138,221,0.4)' : 'rgba(255,255,255,0.1)',
        cursor: 'pointer',
        position: 'relative',
        transition: 'background 0.2s',
        flexShrink: 0,
        border: `1px solid ${enabled ? 'rgba(55,138,221,0.5)' : 'rgba(255,255,255,0.15)'}`,
      }}
    >
      <div style={{
        position: 'absolute',
        top: 2,
        left: enabled ? 13 : 2,
        width: 10,
        height: 10,
        borderRadius: '50%',
        background: enabled ? '#378ADD' : 'rgba(255,255,255,0.3)',
        transition: 'left 0.2s, background 0.2s',
      }} />
    </div>
  )
}

function getStatusColor(status) {
  switch (status) {
    case 'running': return '#639922'
    case 'async': return '#BA7517'
    case 'idle': return 'rgba(255,255,255,0.25)'
    case 'disabled': return 'rgba(255,255,255,0.15)'
    case 'active':
    case 'manager': return '#7F77DD'
    default: return 'rgba(255,255,255,0.4)'
  }
}

function IdentityBlock({ agent }) {
  const did = agent.did || `did:anet:${agent.name}`
  const didParts = did.replace('did:anet:', '').split('::')
  const hashPart = didParts[0] || agent.name
  const namePart = didParts[1] || agent.name

  const registered = agent.registered_at
    ? new Date(agent.registered_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    : '—'

  return (
    <div>
      {/* DID display */}
      <div style={{
        fontFamily: 'monospace',
        fontSize: 9,
        background: 'rgba(255,255,255,0.04)',
        borderRadius: 4,
        padding: '6px 8px',
        wordBreak: 'break-all',
        marginBottom: 8,
        lineHeight: 1.5,
      }}>
        <span style={{ color: '#378ADD' }}>did:anet:</span>
        <span style={{ color: 'rgba(255,255,255,0.6)' }}>{hashPart}</span>
        {didParts[1] && (
          <>
            <span style={{ color: '#378ADD' }}>::</span>
            <span style={{ color: 'rgba(255,255,255,0.85)' }}>{namePart}</span>
          </>
        )}
      </div>
      <InfoRow label="Owner" value={agent.owner || 'system'} />
      <InfoRow label="Network" value={agent.network || 'anet://localhost::v1'} valueColor="rgba(255,255,255,0.5)" />
      <InfoRow label="Registered" value={registered} />
    </div>
  )
}

function ToolsBlock({ agent }) {
  const tools = agent.tools || []
  const asyncTools = tools.filter(t => t.async || (typeof t === 'object' && t.type === 'async'))
  const syncTools = tools.filter(t => !t.async || (typeof t === 'object' && t.type !== 'async'))

  const asyncCount = asyncTools.length || (agent.async_tool_count || 0)

  const getToolName = (t) => typeof t === 'string' ? t : (t.name || String(t))

  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 8 }}>
        {tools.length === 0 && (
          <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.2)' }}>No tools registered</span>
        )}
        {tools.map((tool, i) => {
          const name = getToolName(tool)
          const isAsync = tool.async || tool.type === 'async'
          return (
            <span
              key={i}
              style={{
                fontSize: 9,
                padding: '2px 7px',
                borderRadius: 3,
                border: `1px solid ${isAsync ? 'rgba(186,117,23,0.2)' : 'rgba(55,138,221,0.2)'}`,
                background: isAsync ? 'rgba(186,117,23,0.1)' : 'rgba(55,138,221,0.1)',
                color: isAsync ? '#BA7517' : '#378ADD',
              }}
            >
              {name}
            </span>
          )
        })}
      </div>
      <div style={{ fontSize: 9, color: 'rgba(255,255,255,0.25)' }}>
        {asyncCount > 0 ? `${asyncCount} async tool${asyncCount !== 1 ? 's' : ''}` : '0 async'}
      </div>
    </div>
  )
}

function RuntimeBlock({ agent }) {
  const [localEnabled, setLocalEnabled] = useState(agent.status !== 'disabled')
  const toggleAgent = useStore(s => s.toggleAgent)

  // Sync local state when agent changes
  React.useEffect(() => {
    setLocalEnabled(agent.status !== 'disabled')
  }, [agent.status])

  const handleToggle = () => {
    const next = !localEnabled
    setLocalEnabled(next) // optimistic
    toggleAgent(agent.name)
  }

  return (
    <div>
      <InfoRow label="Model" value={agent.model || '—'} valueColor="#378ADD" />
      <InfoRow label="Provider" value={agent.provider || '—'} />
      <InfoRow label="Status" value={agent.status || '—'} valueColor={getStatusColor(agent.status)} />
      <InfoRow label="Timeout" value={agent.timeout ? `${agent.timeout}s` : '—'} />
      <InfoRow label="Retries" value={agent.retries != null ? String(agent.retries) : '—'} />
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginTop: 8,
        paddingTop: 8,
        borderTop: '1px solid rgba(255,255,255,0.05)',
      }}>
        <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.4)' }}>Enabled</span>
        <ToggleSwitch enabled={localEnabled} onToggle={handleToggle} />
      </div>
    </div>
  )
}

function CurrentTaskBlock({ agent }) {
  const task = agent.current_task || null

  if (!task) {
    return (
      <div style={{ textAlign: 'center', padding: '8px 0', fontSize: 10, color: 'rgba(255,255,255,0.15)' }}>
        no active task
      </div>
    )
  }

  const elapsed = task.started_at
    ? Math.round((Date.now() - new Date(task.started_at).getTime()) / 1000)
    : null

  return (
    <div>
      <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.7)', marginBottom: 6, lineHeight: 1.4 }}>
        {task.description || task.name || 'Running task'}
      </div>
      {(task.step != null && task.total_steps != null) && (
        <div style={{ marginBottom: 4 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
            <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.3)' }}>
              Step {task.step} / {task.total_steps}
            </span>
            {elapsed != null && (
              <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.25)' }}>{elapsed}s elapsed</span>
            )}
          </div>
          {/* Progress bar */}
          <div style={{
            height: 2,
            background: 'rgba(255,255,255,0.06)',
            borderRadius: 1,
            overflow: 'hidden',
          }}>
            <div style={{
              height: '100%',
              width: `${Math.round((task.step / task.total_steps) * 100)}%`,
              background: '#378ADD',
              borderRadius: 1,
              transition: 'width 0.3s',
            }} />
          </div>
        </div>
      )}
      {elapsed != null && !(task.step != null && task.total_steps != null) && (
        <div style={{ fontSize: 9, color: 'rgba(255,255,255,0.25)' }}>{elapsed}s elapsed</div>
      )}
    </div>
  )
}

export default function RightPanel() {
  const selectedAgent = useStore(s => s.selectedAgent)

  return (
    <div style={{
      width: 220,
      minWidth: 220,
      height: '100%',
      background: '#0d0f12',
      borderLeft: '1px solid rgba(255,255,255,0.06)',
      overflowY: 'auto',
      padding: 12,
      flexShrink: 0,
    }}>
      {!selectedAgent ? (
        <div style={{
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 8,
        }}>
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
            <rect x="3" y="3" width="5" height="5" rx="1" stroke="rgba(255,255,255,0.15)" strokeWidth="1.2" />
            <rect x="10" y="3" width="5" height="5" rx="1" stroke="rgba(255,255,255,0.15)" strokeWidth="1.2" />
            <rect x="3" y="10" width="5" height="5" rx="1" stroke="rgba(255,255,255,0.15)" strokeWidth="1.2" />
            <rect x="10" y="10" width="5" height="5" rx="1" stroke="rgba(255,255,255,0.15)" strokeWidth="1.2" />
          </svg>
          <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.15)' }}>
            select an agent
          </span>
        </div>
      ) : (
        <div>
          {/* Agent name header */}
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 500, color: '#e2e4e8', marginBottom: 2 }}>
              {selectedAgent.name.replace(/_agent$/i, '')}
            </div>
            <div style={{ fontSize: 9, color: 'rgba(255,255,255,0.3)' }}>{selectedAgent.name}</div>
          </div>

          <CollapsibleBlock title="Identity">
            <IdentityBlock agent={selectedAgent} />
          </CollapsibleBlock>

          <CollapsibleBlock title="Tools">
            <ToolsBlock agent={selectedAgent} />
          </CollapsibleBlock>

          <CollapsibleBlock title="Runtime">
            <RuntimeBlock agent={selectedAgent} />
          </CollapsibleBlock>

          <CollapsibleBlock title="Current Task">
            <CurrentTaskBlock agent={selectedAgent} />
          </CollapsibleBlock>
        </div>
      )}
    </div>
  )
}
