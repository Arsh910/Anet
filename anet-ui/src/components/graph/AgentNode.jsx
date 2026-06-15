import React from 'react'
import { Handle, Position } from 'reactflow'
import AgentIcon from '../../icons'

export default function AgentNode({ data, selected }) {
  const { agent } = data

  // Ephemeral "spawned" pill node
  if (agent.variant === 'spawn') {
    return (
      <div className="node-spawn">
        <Handle type="target" position={Position.Bottom} />
        {agent.name} <span className="tag">spawned</span>
      </div>
    )
  }

  const cls = [
    'node-card',
    agent.isManager ? 'manager' : '',
    agent.status === 'active' ? 'active' : '',
    agent.status === 'disabled' ? 'dimmed' : '',
  ].join(' ')

  return (
    <div className={cls}>
      <Handle type="target" position={Position.Top} />
      <Handle type="source" position={Position.Bottom} />
      <div className="node-head">
        <div className="node-icon"><AgentIcon name={agent.icon} size={16} /></div>
        <div className="node-title">
          <div className="node-name">
            {agent.name}
            {agent.isManager && <span className="tag-mgr">MGR</span>}
          </div>
          <div className="node-model">{agent.model}</div>
        </div>
        <span className={`dot ${agent.status} ${agent.status === 'active' ? 'pulse' : ''}`} />
      </div>
    </div>
  )
}
