import React from 'react'
import { BaseEdge, getBezierPath } from 'reactflow'

export default function AgentEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data = {},
}) {
  const [edgePath] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition })

  const { active, asyncWait } = data

  let lineStyle
  if (active) {
    lineStyle = {
      stroke: '#378ADD',
      strokeWidth: 1.5,
      opacity: 0.35,
    }
  } else if (asyncWait) {
    lineStyle = {
      stroke: 'rgba(186,117,23,0.4)',
      strokeWidth: 1,
      strokeDasharray: '4 4',
    }
  } else {
    lineStyle = {
      stroke: 'rgba(255,255,255,0.07)',
      strokeWidth: 1,
      strokeDasharray: '3 5',
    }
  }

  const markerEnd = active
    ? `url(#arrowhead-active-${id})`
    : asyncWait
    ? `url(#arrowhead-async-${id})`
    : `url(#arrowhead-idle-${id})`

  return (
    <>
      {/* Defs for arrowheads and path */}
      <defs>
        {active && (
          <marker
            id={`arrowhead-active-${id}`}
            markerWidth="8"
            markerHeight="8"
            refX="6"
            refY="3"
            orient="auto"
          >
            <path d="M0,0 L0,6 L8,3 z" fill="#378ADD" opacity="0.5" />
          </marker>
        )}
        {asyncWait && (
          <marker
            id={`arrowhead-async-${id}`}
            markerWidth="8"
            markerHeight="8"
            refX="6"
            refY="3"
            orient="auto"
          >
            <path d="M0,0 L0,6 L8,3 z" fill="rgba(186,117,23,0.5)" />
          </marker>
        )}
        {!active && !asyncWait && (
          <marker
            id={`arrowhead-idle-${id}`}
            markerWidth="8"
            markerHeight="8"
            refX="6"
            refY="3"
            orient="auto"
          >
            <path d="M0,0 L0,6 L8,3 z" fill="rgba(255,255,255,0.07)" />
          </marker>
        )}
      </defs>

      {/* Hidden path for animateMotion reference */}
      <path id={id} d={edgePath} fill="none" stroke="none" />

      {/* Visible edge */}
      <BaseEdge
        path={edgePath}
        style={lineStyle}
        markerEnd={markerEnd}
      />

      {/* Particles for active edges */}
      {active && [0, 700, 1400].map((delay, i) => (
        <circle key={i} r="2.5" fill="#378ADD" opacity="0.8">
          <animateMotion
            dur="2s"
            repeatCount="indefinite"
            begin={`${delay}ms`}
          >
            <mpath href={`#${id}`} />
          </animateMotion>
        </circle>
      ))}
    </>
  )
}
