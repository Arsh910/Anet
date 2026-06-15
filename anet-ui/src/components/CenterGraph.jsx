import React, { useMemo, useState } from 'react'
import ReactFlow, {
  Background, BackgroundVariant, ReactFlowProvider, useReactFlow, MarkerType,
} from 'reactflow'
import 'reactflow/dist/style.css'
import { Minus, Plus, Maximize2, Circle } from 'lucide-react'
import { useStore } from '../store/useStore'
import AgentNode from './graph/AgentNode'

const nodeTypes = { agentNode: AgentNode }

// Fractional positions (from design.js) mapped onto a virtual canvas.
const POS = {
  manager:         { x: 0.50, y: 0.55 },
  code:            { x: 0.50, y: 0.18 },
  research:        { x: 0.84, y: 0.36 },
  checker:         { x: 0.40, y: 0.80 },
  file:            { x: 0.84, y: 0.78 },
  computer:        { x: 0.52, y: 0.96 },
  'design-critic': { x: 0.14, y: 0.36 },
}
const CANVAS = { w: 900, h: 560 }

function colorFor(state) {
  if (state === 'active') return 'var(--accent)'
  if (state === 'disabled') return 'rgba(120,120,128,0.18)'
  return 'rgba(150,150,160,0.28)'
}

function GraphInner() {
  const { agents, edges, setSelectedAgentId } = useStore()
  const { zoomIn, zoomOut, fitView } = useReactFlow()
  const [zoom, setZoom] = useState(100)

  const flowNodes = useMemo(() => {
    const nodes = agents
      .filter(a => POS[a.id])
      .map(a => ({
        id: a.id,
        type: 'agentNode',
        position: { x: POS[a.id].x * CANVAS.w, y: POS[a.id].y * CANVAS.h },
        data: { agent: a },
      }))
    // ephemeral spawned node above Code
    nodes.push({
      id: 'test-runner',
      type: 'agentNode',
      position: { x: 0.52 * CANVAS.w, y: 0.02 * CANVAS.h },
      data: { agent: { id: 'test-runner', name: 'test-runner', variant: 'spawn' } },
    })
    return nodes
  }, [agents])

  const flowEdges = useMemo(() => {
    const out = edges.map(e => ({
      id: `${e.from}-${e.to}`,
      source: e.from,
      target: e.to,
      className: e.state === 'active' ? 'active' : '',
      animated: false,
      style: {
        stroke: colorFor(e.state),
        strokeWidth: e.state === 'active' ? 1.8 : 1.2,
        strokeDasharray: e.state === 'idle' ? '4 5' : undefined,
        opacity: e.state === 'disabled' ? 0.5 : 1,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color: colorFor(e.state), width: 14, height: 14 },
    }))
    // code -> spawned test-runner (vertical dashed)
    out.push({
      id: 'code-test-runner',
      source: 'code',
      target: 'test-runner',
      className: 'active',
      style: { stroke: 'var(--accent)', strokeWidth: 1.6, strokeDasharray: '5 4' },
    })
    return out
  }, [edges])

  const onNodeClick = (_e, node) => {
    if (node.id !== 'test-runner') setSelectedAgentId(node.id)
  }

  const onMove = (_e, vp) => setZoom(Math.round(vp.zoom * 100))

  return (
    <>
      <div className="graph-tab"><Circle size={8} fill="var(--accent)" color="var(--accent)" /> Orchestration graph</div>
      <div className="graph-toolbar">
        <button className="icon-btn" onClick={() => zoomOut()} title="Zoom out"><Minus size={15} /></button>
        <span className="zoom-val">{zoom}%</span>
        <button className="icon-btn" onClick={() => zoomIn()} title="Zoom in"><Plus size={15} /></button>
        <button className="icon-btn" onClick={() => fitView({ padding: 0.2, duration: 300 })} title="Fit"><Maximize2 size={14} /></button>
      </div>
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        onMove={onMove}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        minZoom={0.4}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        nodesDraggable
        nodesConnectable={false}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="rgba(150,150,160,0.08)" />
      </ReactFlow>
    </>
  )
}

export default function CenterGraph() {
  return (
    <div className="graph-wrap">
      <ReactFlowProvider>
        <GraphInner />
      </ReactFlowProvider>
    </div>
  )
}
