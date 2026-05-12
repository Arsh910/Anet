import React, { useState } from 'react'
import { useStore } from '../store/useStore'

const MODEL_OPTIONS = [
  { value: 'gemini-2.5-flash', label: 'gemini-2.5-flash' },
  { value: 'gemini-2.5-pro', label: 'gemini-2.5-pro' },
  { value: 'claude-sonnet-4-5', label: 'claude-sonnet-4-5' },
  { value: 'claude-opus-4-5', label: 'claude-opus-4-5' },
  { value: 'gpt-4o', label: 'gpt-4o' },
]

function modelToProvider(model) {
  if (!model) return ''
  if (model.startsWith('gemini')) return 'google'
  if (model.startsWith('claude')) return 'anthropic'
  if (model.startsWith('gpt')) return 'openai'
  return ''
}

const inputStyle = {
  width: '100%',
  background: 'rgba(255,255,255,0.06)',
  border: '1px solid rgba(255,255,255,0.1)',
  borderRadius: 4,
  padding: '7px 10px',
  color: '#e2e4e8',
  fontSize: 11,
  fontFamily: 'inherit',
  outline: '1px solid transparent',
  transition: 'outline 0.15s',
  boxSizing: 'border-box',
}

const labelStyle = {
  display: 'block',
  fontSize: 9,
  color: 'rgba(255,255,255,0.3)',
  textTransform: 'uppercase',
  letterSpacing: 0.8,
  marginBottom: 5,
}

function FieldLabel({ children }) {
  return <label style={labelStyle}>{children}</label>
}

function InputField({ value, onChange, placeholder, style, ...rest }) {
  return (
    <input
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      style={{ ...inputStyle, ...style }}
      onFocus={e => e.target.style.outline = '1px solid rgba(55,138,221,0.5)'}
      onBlur={e => e.target.style.outline = '1px solid transparent'}
      {...rest}
    />
  )
}

export default function AddAgentModal({ onClose }) {
  const { registerAgent, scanPath } = useStore()

  const [name, setName] = useState('')
  const [path, setPath] = useState('')
  const [model, setModel] = useState('gemini-2.5-flash')
  const [provider, setProvider] = useState('google')
  const [description, setDescription] = useState('')
  const [taskTypes, setTaskTypes] = useState([])
  const [taskInput, setTaskInput] = useState('')
  const [scannedTools, setScannedTools] = useState([])
  const [scanning, setScanning] = useState(false)
  const [scanError, setScanError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const handleModelChange = (e) => {
    const v = e.target.value
    setModel(v)
    setProvider(modelToProvider(v))
  }

  const handleScan = async () => {
    if (!path.trim()) return
    setScanning(true)
    setScanError('')
    setScannedTools([])
    try {
      const result = await scanPath(path.trim())
      setScannedTools(result.tools || [])
      if (result.name && !name) setName(result.name)
    } catch (err) {
      setScanError(err.message || 'Scan failed')
    } finally {
      setScanning(false)
    }
  }

  const handleTaskKeyDown = (e) => {
    if (e.key === 'Enter' && taskInput.trim()) {
      e.preventDefault()
      if (!taskTypes.includes(taskInput.trim())) {
        setTaskTypes(prev => [...prev, taskInput.trim()])
      }
      setTaskInput('')
    }
  }

  const removeTaskType = (tag) => {
    setTaskTypes(prev => prev.filter(t => t !== tag))
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!path.trim()) {
      setError('Folder path is required')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      await registerAgent(path.trim())
      onClose()
    } catch (err) {
      setError(err.message || 'Registration failed')
    } finally {
      setSubmitting(false)
    }
  }

  // Close on overlay click
  const handleOverlayClick = (e) => {
    if (e.target === e.currentTarget) onClose()
  }

  return (
    <div
      onClick={handleOverlayClick}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.7)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div style={{
        background: '#161a20',
        border: '1px solid rgba(255,255,255,0.1)',
        borderRadius: 8,
        width: 400,
        maxHeight: '85vh',
        overflowY: 'auto',
        padding: 20,
        animation: 'fadeIn 0.15s ease',
      }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
          <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.6)', textTransform: 'uppercase', letterSpacing: 0.8, fontWeight: 500 }}>
            Add Agent
          </span>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'rgba(255,255,255,0.3)',
              cursor: 'pointer',
              padding: 0,
              fontSize: 16,
              lineHeight: 1,
              fontFamily: 'inherit',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 24,
              height: 24,
              borderRadius: 4,
              transition: 'color 0.15s, background 0.15s',
            }}
            onMouseEnter={e => {
              e.currentTarget.style.color = '#e2e4e8'
              e.currentTarget.style.background = 'rgba(255,255,255,0.06)'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.color = 'rgba(255,255,255,0.3)'
              e.currentTarget.style.background = 'transparent'
            }}
          >
            ×
          </button>
        </div>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Agent name */}
          <div>
            <FieldLabel>Agent Name</FieldLabel>
            <InputField
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. research_agent"
            />
          </div>

          {/* Folder path + scan */}
          <div>
            <FieldLabel>Folder Path</FieldLabel>
            <div style={{ display: 'flex', gap: 6 }}>
              <InputField
                value={path}
                onChange={e => setPath(e.target.value)}
                placeholder="/path/to/agent"
                style={{ flex: 1 }}
              />
              <button
                type="button"
                onClick={handleScan}
                disabled={scanning || !path.trim()}
                style={{
                  background: 'rgba(55,138,221,0.1)',
                  border: '1px solid rgba(55,138,221,0.2)',
                  borderRadius: 4,
                  color: scanning ? 'rgba(55,138,221,0.4)' : '#378ADD',
                  fontSize: 9,
                  letterSpacing: 0.8,
                  padding: '0 10px',
                  cursor: scanning || !path.trim() ? 'default' : 'pointer',
                  fontFamily: 'inherit',
                  textTransform: 'uppercase',
                  transition: 'background 0.15s',
                  whiteSpace: 'nowrap',
                  flexShrink: 0,
                }}
                onMouseEnter={e => { if (!scanning && path.trim()) e.currentTarget.style.background = 'rgba(55,138,221,0.18)' }}
                onMouseLeave={e => e.currentTarget.style.background = 'rgba(55,138,221,0.1)'}
              >
                {scanning ? '…' : 'SCAN'}
              </button>
            </div>
            {scanError && (
              <div style={{ fontSize: 9, color: '#E24B4A', marginTop: 4 }}>{scanError}</div>
            )}
            {scannedTools.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
                <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.25)', width: '100%', marginBottom: 2 }}>
                  Found {scannedTools.length} tool{scannedTools.length !== 1 ? 's' : ''}:
                </span>
                {scannedTools.map((tool, i) => {
                  const toolName = typeof tool === 'string' ? tool : (tool.name || String(tool))
                  return (
                    <span
                      key={i}
                      style={{
                        fontSize: 9,
                        padding: '2px 7px',
                        borderRadius: 3,
                        background: 'rgba(55,138,221,0.1)',
                        border: '1px solid rgba(55,138,221,0.2)',
                        color: '#378ADD',
                      }}
                    >
                      {toolName}
                    </span>
                  )
                })}
              </div>
            )}
          </div>

          {/* Model */}
          <div>
            <FieldLabel>Model</FieldLabel>
            <select
              value={model}
              onChange={handleModelChange}
              style={{
                ...inputStyle,
                cursor: 'pointer',
                appearance: 'none',
                backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 10 10'%3E%3Cpath d='M2 3.5 L5 6.5 L8 3.5' stroke='rgba(255,255,255,0.3)' stroke-width='1.2' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")`,
                backgroundRepeat: 'no-repeat',
                backgroundPosition: 'right 10px center',
                paddingRight: 28,
              }}
              onFocus={e => e.target.style.outline = '1px solid rgba(55,138,221,0.5)'}
              onBlur={e => e.target.style.outline = '1px solid transparent'}
            >
              {MODEL_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value} style={{ background: '#161a20', color: '#e2e4e8' }}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          {/* Provider (auto-filled) */}
          <div>
            <FieldLabel>Provider</FieldLabel>
            <InputField
              value={provider}
              onChange={e => setProvider(e.target.value)}
              placeholder="google / anthropic / openai"
            />
          </div>

          {/* Task types (tag input) */}
          <div>
            <FieldLabel>Task Types</FieldLabel>
            {taskTypes.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 6 }}>
                {taskTypes.map(tag => (
                  <span
                    key={tag}
                    style={{
                      fontSize: 9,
                      padding: '2px 7px',
                      borderRadius: 3,
                      background: 'rgba(127,119,221,0.1)',
                      border: '1px solid rgba(127,119,221,0.2)',
                      color: '#7F77DD',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 4,
                    }}
                  >
                    {tag}
                    <span
                      onClick={() => removeTaskType(tag)}
                      style={{ cursor: 'pointer', color: 'rgba(127,119,221,0.5)', lineHeight: 1 }}
                      onMouseEnter={e => e.currentTarget.style.color = '#7F77DD'}
                      onMouseLeave={e => e.currentTarget.style.color = 'rgba(127,119,221,0.5)'}
                    >
                      ×
                    </span>
                  </span>
                ))}
              </div>
            )}
            <InputField
              value={taskInput}
              onChange={e => setTaskInput(e.target.value)}
              onKeyDown={handleTaskKeyDown}
              placeholder="Type and press Enter to add…"
            />
          </div>

          {/* Description */}
          <div>
            <FieldLabel>Description</FieldLabel>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Briefly describe what this agent does…"
              rows={2}
              style={{
                ...inputStyle,
                resize: 'vertical',
                minHeight: 52,
                lineHeight: 1.5,
              }}
              onFocus={e => e.target.style.outline = '1px solid rgba(55,138,221,0.5)'}
              onBlur={e => e.target.style.outline = '1px solid transparent'}
            />
          </div>

          {/* Error */}
          {error && (
            <div style={{
              fontSize: 10,
              color: '#E24B4A',
              background: 'rgba(226,75,74,0.08)',
              border: '1px solid rgba(226,75,74,0.2)',
              borderRadius: 4,
              padding: '6px 10px',
            }}>
              {error}
            </div>
          )}

          {/* Submit */}
          <button
            type="submit"
            disabled={submitting}
            style={{
              width: '100%',
              padding: '9px 0',
              background: submitting ? 'rgba(55,138,221,0.08)' : 'rgba(55,138,221,0.15)',
              border: '1px solid rgba(55,138,221,0.3)',
              borderRadius: 4,
              color: submitting ? 'rgba(55,138,221,0.4)' : '#378ADD',
              fontSize: 10,
              letterSpacing: 0.8,
              textTransform: 'uppercase',
              cursor: submitting ? 'default' : 'pointer',
              fontFamily: 'inherit',
              fontWeight: 500,
              transition: 'background 0.15s',
              marginTop: 2,
            }}
            onMouseEnter={e => { if (!submitting) e.currentTarget.style.background = 'rgba(55,138,221,0.25)' }}
            onMouseLeave={e => { if (!submitting) e.currentTarget.style.background = 'rgba(55,138,221,0.15)' }}
          >
            {submitting ? 'Registering…' : 'Register Agent'}
          </button>
        </form>
      </div>
    </div>
  )
}
