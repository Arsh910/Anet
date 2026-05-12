import React, { useEffect, useRef } from 'react'
import { useStore } from '../store/useStore'

function getMessageColor(type) {
  if (!type) return 'rgba(255,255,255,0.45)'
  const t = type.toLowerCase()
  if (t === 'ok' || t === 'success') return '#639922'
  if (t === 'run') return '#BA7517'
  if (t === 'err' || t === 'error') return '#E24B4A'
  if (t === 'info') return '#378ADD'
  return 'rgba(255,255,255,0.45)'
}

function formatTimestamp(raw) {
  if (!raw) return '--:--:--'
  // If it's already a time string like HH:MM:SS return as-is
  if (typeof raw === 'string' && /^\d{2}:\d{2}(:\d{2})?$/.test(raw)) return raw
  // If ISO string or timestamp
  try {
    const d = new Date(raw)
    if (isNaN(d)) return String(raw).slice(0, 8)
    return d.toTimeString().slice(0, 8)
  } catch {
    return String(raw).slice(0, 8)
  }
}

export default function BottomLog() {
  const logs = useStore(s => s.logs)
  const scrollRef = useRef(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [logs])

  return (
    <div style={{
      height: 100,
      minHeight: 100,
      width: '100%',
      background: '#0b0d10',
      borderTop: '1px solid rgba(255,255,255,0.06)',
      display: 'flex',
      flexDirection: 'column',
      flexShrink: 0,
    }}>
      {/* Header */}
      <div style={{
        height: 28,
        minHeight: 28,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 12px',
        borderBottom: '1px solid rgba(255,255,255,0.04)',
        flexShrink: 0,
      }}>
        <span style={{
          fontSize: 9,
          color: 'rgba(255,255,255,0.2)',
          textTransform: 'uppercase',
          letterSpacing: 0.8,
        }}>
          Live Execution Log
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <div style={{
            width: 5,
            height: 5,
            borderRadius: '50%',
            background: '#639922',
            animation: 'pulse-fast 1.5s infinite',
          }} />
          <span style={{
            fontSize: 9,
            color: 'rgba(99,153,34,0.7)',
            letterSpacing: 0.6,
          }}>
            STREAMING
          </span>
        </div>
      </div>

      {/* Log rows */}
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflowY: 'auto',
          minHeight: 0,
        }}
      >
        {logs.length === 0 && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            height: '100%',
            fontSize: 9,
            color: 'rgba(255,255,255,0.1)',
          }}>
            Waiting for log events…
          </div>
        )}
        {logs.map((entry, idx) => (
          <LogRow key={idx} entry={entry} />
        ))}
      </div>
    </div>
  )
}

function LogRow({ entry }) {
  const msgColor = getMessageColor(entry.type || entry.level)
  const ts = formatTimestamp(entry.time || entry.timestamp || entry.ts)

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        padding: '2px 12px',
        gap: 10,
        animation: 'fadeIn 0.15s ease',
        minHeight: 18,
      }}
      onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.025)'}
      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
    >
      <span style={{
        fontSize: 9,
        fontFamily: 'monospace',
        color: 'rgba(255,255,255,0.2)',
        minWidth: 52,
        flexShrink: 0,
        lineHeight: 1.6,
      }}>
        {ts}
      </span>
      <span style={{
        fontSize: 9,
        fontFamily: 'monospace',
        color: '#7F77DD',
        minWidth: 90,
        flexShrink: 0,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        lineHeight: 1.6,
      }}>
        {entry.agent || entry.source || '—'}
      </span>
      <span style={{
        fontSize: 9,
        color: msgColor,
        lineHeight: 1.6,
        wordBreak: 'break-word',
      }}>
        {entry.message || entry.msg || entry.text || String(entry)}
      </span>
    </div>
  )
}
