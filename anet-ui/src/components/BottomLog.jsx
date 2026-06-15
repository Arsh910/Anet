import React, { useEffect, useRef } from 'react'
import { Activity, Trash2, ChevronDown } from 'lucide-react'
import { useStore } from '../store/useStore'

const FILTERS = ['All', 'Tools', 'Errors']

export default function BottomLog() {
  const {
    logs, logFilter, setLogFilter, autoScroll, toggleAutoScroll,
    clearLogs, activityCollapsed, toggleActivity,
  } = useStore()
  const scrollRef = useRef(null)

  const filtered = logs.filter(l => {
    if (logFilter === 'Tools') return l.level === 'TOOL'
    if (logFilter === 'Errors') return l.level === 'ERR' || l.level === 'WARN'
    return true
  })

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [filtered, autoScroll])

  return (
    <div className={`panel activity ${activityCollapsed ? 'collapsed' : ''}`}>
      <div className="panel-header">
        <Activity size={14} color="var(--accent)" />
        <span className="panel-title">Activity</span>
        <div className="filter-tabs" style={{ marginLeft: 8 }}>
          {FILTERS.map(f => (
            <button
              key={f}
              className={`filter-tab ${logFilter === f ? 'active' : ''}`}
              onClick={() => setLogFilter(f)}
            >{f}</button>
          ))}
        </div>
        <div className="spacer" />
        <button className="conn" onClick={toggleAutoScroll} title="Toggle auto-scroll" style={{ background: 'none' }}>
          <span className={`dot ${autoScroll ? 'success' : 'idle'}`} /> auto-scroll
        </button>
        <button className="icon-btn" onClick={clearLogs} title="Clear"><Trash2 size={14} /></button>
        <button className="icon-btn" onClick={toggleActivity} title="Collapse"
          style={{ transform: activityCollapsed ? 'rotate(180deg)' : 'none' }}>
          <ChevronDown size={15} />
        </button>
      </div>

      {!activityCollapsed && (
        <div className="log-body" ref={scrollRef}>
          {filtered.length === 0 && <div className="empty">No activity yet.</div>}
          {filtered.map((l, i) => (
            <div className="log-row" key={i}>
              <span className="log-time">{l.time}</span>
              <span className={`log-level lvl-${l.level}`}>{l.level}</span>
              <span className="log-agent">{l.agent}</span>
              <span className="log-msg">
                {l.message}
                {l.chip && <span className="log-chip">{l.chip}</span>}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
