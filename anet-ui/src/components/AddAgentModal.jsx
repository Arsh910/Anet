import React, { useState } from 'react'
import { X, FolderSearch } from 'lucide-react'
import { useStore } from '../store/useStore'

export default function AddAgentModal() {
  const { setShowAddAgent, registerAgent, scanPath } = useStore()
  const [path, setPath] = useState('')
  const [tools, setTools] = useState([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const close = () => setShowAddAgent(false)

  const handleScan = async () => {
    if (!path.trim()) return
    setError(''); setTools([])
    try {
      const res = await scanPath(path.trim())
      setTools(res.tools || [])
    } catch (e) {
      setError(e.message || 'Path not found')
    }
  }

  const handleAdd = async () => {
    if (!path.trim()) { setError('Folder path is required'); return }
    setBusy(true); setError('')
    try {
      await registerAgent(path.trim())
      close()
    } catch (e) {
      setError(e.message || 'Registration failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && close()}>
      <div className="modal">
        <div className="row-between">
          <h3>Add agent</h3>
          <button className="icon-btn" onClick={close}><X size={16} /></button>
        </div>

        <div className="field">
          <label>Agent folder path</label>
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              className="text-input"
              style={{ flex: 1 }}
              placeholder="C:\path\to\agent  or  ExAgents/my_agent"
              value={path}
              onChange={(e) => setPath(e.target.value)}
            />
            <button className="btn btn-outline" onClick={handleScan}>
              <FolderSearch size={15} /> Scan
            </button>
          </div>
        </div>

        {tools.length > 0 && (
          <div className="field">
            <label>Detected tools</label>
            <div className="tools-wrap" style={{ marginTop: 0 }}>
              {tools.map((t, i) => <span className="chip" key={i}>{typeof t === 'string' ? t : t.name}</span>)}
            </div>
          </div>
        )}

        {error && <div className="modal-err">{error}</div>}

        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={close}>Cancel</button>
          <button className="btn btn-primary" onClick={handleAdd} disabled={busy}>
            {busy ? 'Adding…' : 'Add agent'}
          </button>
        </div>
      </div>
    </div>
  )
}
