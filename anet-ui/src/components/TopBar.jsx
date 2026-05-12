import React, { useState, useEffect } from 'react'

const TABS = ['Graph', 'Tasks', 'Agents', 'Identity', 'Settings']

function formatTime(date) {
  const h = String(date.getHours()).padStart(2, '0')
  const m = String(date.getMinutes()).padStart(2, '0')
  return `${h}:${m}`
}

export default function TopBar() {
  const [activeTab, setActiveTab] = useState('Graph')
  const [time, setTime] = useState(formatTime(new Date()))

  useEffect(() => {
    const tick = () => setTime(formatTime(new Date()))
    const id = setInterval(tick, 60000)
    // sync to next minute boundary
    const now = new Date()
    const msToNext = (60 - now.getSeconds()) * 1000 - now.getMilliseconds()
    const timeout = setTimeout(() => {
      tick()
    }, msToNext)
    return () => { clearInterval(id); clearTimeout(timeout) }
  }, [])

  return (
    <div style={{
      height: 44,
      minHeight: 44,
      background: '#0d0f12',
      borderBottom: '1px solid rgba(255,255,255,0.06)',
      display: 'flex',
      alignItems: 'center',
      padding: '0 16px',
      gap: 0,
      flexShrink: 0,
    }}>
      {/* Left: brand */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 160 }}>
        <div style={{
          width: 12,
          height: 12,
          borderRadius: '50%',
          background: '#7F77DD',
          flexShrink: 0,
        }} />
        <span style={{ fontSize: 13, fontWeight: 500, color: '#e2e4e8', letterSpacing: 0.3 }}>ANet</span>
        <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.3)', marginLeft: 2 }}>Intelligence</span>
        <span style={{
          fontSize: 9,
          background: 'rgba(55,138,221,0.15)',
          color: '#378ADD',
          border: '1px solid rgba(55,138,221,0.2)',
          borderRadius: 8,
          padding: '1px 6px',
          marginLeft: 4,
          letterSpacing: 0.2,
        }}>v1.0</span>
      </div>

      {/* Center: tabs */}
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 2 }}>
        {TABS.map(tab => {
          const isActive = tab === activeTab
          return (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                background: 'transparent',
                border: 'none',
                borderBottom: isActive ? '1px solid #378ADD' : '1px solid transparent',
                color: isActive ? '#e2e4e8' : 'rgba(255,255,255,0.35)',
                fontSize: 11,
                fontWeight: isActive ? 500 : 400,
                padding: '0 14px',
                height: 44,
                cursor: 'pointer',
                letterSpacing: 0.2,
                transition: 'color 0.15s',
                outline: 'none',
                fontFamily: 'inherit',
              }}
              onMouseEnter={e => { if (!isActive) e.currentTarget.style.color = 'rgba(255,255,255,0.65)' }}
              onMouseLeave={e => { if (!isActive) e.currentTarget.style.color = 'rgba(255,255,255,0.35)' }}
            >
              {tab}
            </button>
          )
        })}
      </div>

      {/* Right: status + time */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 180, justifyContent: 'flex-end' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <div style={{
            width: 5,
            height: 5,
            borderRadius: '50%',
            background: '#639922',
            animation: 'pulse-fast 2s infinite',
          }} />
          <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.5)', letterSpacing: 0.8, textTransform: 'uppercase' }}>
            System Optimal
          </span>
        </div>
        <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.3)', fontVariantNumeric: 'tabular-nums' }}>
          {time}
        </span>
      </div>
    </div>
  )
}
