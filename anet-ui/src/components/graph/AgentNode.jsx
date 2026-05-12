import React, { useState } from 'react'
import { Handle, Position } from 'reactflow'

function stripSuffix(name) {
  return name ? name.replace(/_agent$/i, '') : name
}

function getStatusStyles(status) {
  switch (status) {
    case 'running':
      return {
        border: '1px solid rgba(99,153,34,0.6)',
        background: 'rgba(99,153,34,0.10)',
        dotColor: '#639922',
        textColor: '#639922',
        ringBorder: '1px solid rgba(99,153,34,0.25)',
        ringAnimation: 'ring-run 1.5s ease-in-out infinite',
      }
    case 'async':
      return {
        border: '1px solid rgba(186,117,23,0.6)',
        background: 'rgba(186,117,23,0.10)',
        dotColor: '#BA7517',
        textColor: '#BA7517',
        ringBorder: '1px solid rgba(186,117,23,0.2)',
        ringAnimation: 'ring-async 2.5s ease-in-out infinite',
      }
    case 'active':
    case 'manager':
      return {
        border: '1px solid rgba(127,119,221,0.6)',
        background: 'rgba(127,119,221,0.12)',
        dotColor: '#7F77DD',
        textColor: '#7F77DD',
        ringBorder: '1px solid rgba(127,119,221,0.2)',
        ringAnimation: 'ring-run 2s ease-in-out infinite',
      }
    case 'idle':
      return {
        border: '1px solid rgba(255,255,255,0.1)',
        background: 'rgba(255,255,255,0.04)',
        dotColor: 'rgba(255,255,255,0.25)',
        textColor: 'rgba(255,255,255,0.5)',
        ringBorder: null,
        ringAnimation: null,
      }
    case 'disabled':
    default:
      return {
        border: '1px solid rgba(255,255,255,0.06)',
        background: '#111416',
        dotColor: 'rgba(255,255,255,0.08)',
        textColor: 'rgba(255,255,255,0.2)',
        ringBorder: null,
        ringAnimation: null,
      }
  }
}

function TooltipContent({ agent }) {
  const rows = [
    { label: 'status', value: agent.status || '—' },
    { label: 'model', value: agent.model || '—' },
    { label: 'tasks today', value: agent.tasks_today != null ? agent.tasks_today : '—' },
    { label: 'tools', value: agent.tools ? agent.tools.length : (agent.tool_count || '—') },
  ]
  return (
    <div style={{
      position: 'absolute',
      zIndex: 100,
      top: -100,
      left: '50%',
      transform: 'translateX(-50%)',
      background: '#161a20',
      border: '1px solid rgba(255,255,255,0.1)',
      borderRadius: 6,
      padding: '8px 10px',
      minWidth: 140,
      pointerEvents: 'none',
      animation: 'fadeIn 0.1s ease',
    }}>
      {rows.map(row => (
        <div key={row.label} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 3 }}>
          <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.3)' }}>{row.label}</span>
          <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.75)' }}>{String(row.value)}</span>
        </div>
      ))}
    </div>
  )
}

function AgentNode({ data }) {
  const { agent, onSelect } = data
  const [showTooltip, setShowTooltip] = useState(false)
  const [hovered, setHovered] = useState(false)

  if (!agent) return null

  const status = agent.status || 'idle'
  const s = getStatusStyles(status)
  const displayName = stripSuffix(agent.name)

  return (
    <div
      style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5, cursor: 'pointer' }}
      onClick={() => onSelect && onSelect(agent)}
      onMouseEnter={() => { setShowTooltip(true); setHovered(true) }}
      onMouseLeave={() => { setShowTooltip(false); setHovered(false) }}
    >
      <div style={{ position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {/* Outer ring for running/async/active */}
        {s.ringBorder && (
          <div style={{
            position: 'absolute',
            inset: -6,
            borderRadius: '50%',
            border: s.ringBorder,
            animation: s.ringAnimation,
            pointerEvents: 'none',
          }} />
        )}

        {/* Hover ring */}
        {hovered && (
          <div style={{
            position: 'absolute',
            inset: -4,
            borderRadius: '50%',
            border: '1px solid rgba(55,138,221,0.35)',
            pointerEvents: 'none',
          }} />
        )}

        {/* Main circle */}
        <div style={{
          width: 48,
          height: 48,
          borderRadius: '50%',
          border: s.border,
          background: s.background,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          position: 'relative',
          transition: 'border-color 0.15s',
        }}>
          {/* Agent initial(s) */}
          <span style={{
            fontSize: 13,
            fontWeight: 500,
            color: s.textColor,
            userSelect: 'none',
            lineHeight: 1,
          }}>
            {displayName ? displayName.charAt(0).toUpperCase() : '?'}
          </span>

          {/* Status dot */}
          <div style={{
            position: 'absolute',
            top: -2,
            right: -2,
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: s.dotColor,
            border: '1.5px solid #0b0d10',
          }} />
        </div>

        {/* Tooltip */}
        {showTooltip && <TooltipContent agent={agent} />}

        {/* ReactFlow Handles */}
        <Handle
          type="target"
          position={Position.Top}
          style={{ opacity: 0, width: 1, height: 1, minWidth: 0, minHeight: 0, border: 'none', background: 'transparent' }}
        />
        <Handle
          type="source"
          position={Position.Bottom}
          style={{ opacity: 0, width: 1, height: 1, minWidth: 0, minHeight: 0, border: 'none', background: 'transparent' }}
        />
      </div>

      {/* Label */}
      <div style={{ textAlign: 'center' }}>
        <div style={{ fontSize: 9, color: s.textColor, fontWeight: 500, letterSpacing: 0.2 }}>
          {displayName}
        </div>
        <div style={{ fontSize: 8, color: 'rgba(255,255,255,0.25)', marginTop: 1 }}>
          {status}
        </div>
      </div>
    </div>
  )
}

export default AgentNode
