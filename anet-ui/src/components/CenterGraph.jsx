import React, { useMemo, useCallback, useEffect, useRef } from 'react'
import ReactFlow, {
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  ReactFlowProvider,
  useReactFlow,
  BackgroundVariant,
} from 'reactflow'
import 'reactflow/dist/style.css'
import { useStore } from '../store/useStore'
import AgentNode from './graph/AgentNode'
import AgentEdge from './graph/AgentEdge'

const nodeTypes = { agentNode: AgentNode }
const edgeTypes = { agentEdge: AgentEdge }

const MANAGER_NODE = {
  id: 'manager',
  type: 'agentNode',
  position: { x: 0, y: 0 },
  data: {
    agent: {
      name: 'manager',
      status: 'active',
      model: 'system',
      tasks_today: null,
      tools: [],
    },
  },
}

function buildLayout(agents, containerWidth) {
  const width = containerWidth || 800
  const centerX = width / 2

  // Manager at top center (subtract half node width ~24px)
  const managerX = centerX - 24
  const managerY = 40

  const COLS = 4
  const H_SPACING = 130
  const V_SPACING = 120

  const nodes = [
    {
      ...MANAGER_NODE,
      position: { x: managerX, y: managerY },
    },
  ]

  agents.forEach((agent, idx) => {
    const row = Math.floor(idx / COLS)
    const col = idx % COLS
    const rowCount = Math.min(COLS, agents.length - row * COLS)
    const rowWidth = (rowCount - 1) * H_SPACING
    const rowStartX = centerX - rowWidth / 2 - 24
    const x = rowStartX + col * H_SPACING
    const y = managerY + 160 + row * V_SPACING

    nodes.push({
      id: agent.name,
      type: 'agentNode',
      position: { x, y },
      data: { agent },
    })
  })

  return nodes
}

function buildEdges(agents, setSelectedAgent) {
  return agents.map(agent => {
    const isActive = agent.status === 'running' || agent.status === 'async'
    const isAsync = agent.status === 'async'
    return {
      id: `manager-${agent.name}`,
      source: 'manager',
      target: agent.name,
      type: 'agentEdge',
      data: { active: isActive, asyncWait: isAsync },
    }
  })
}

function GraphInner() {
  const agents = useStore(s => s.agents)
  const setSelectedAgent = useStore(s => s.setSelectedAgent)
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const { fitView } = useReactFlow()
  const containerRef = useRef(null)
  const didFitRef = useRef(false)

  // Inject onSelect into node data
  const agentsWithSelect = useMemo(() =>
    agents.map(a => ({ ...a })),
    [agents]
  )

  const containerWidth = containerRef.current?.offsetWidth || 800

  useEffect(() => {
    const builtNodes = buildLayout(agentsWithSelect, containerWidth).map(n => ({
      ...n,
      data: {
        ...n.data,
        onSelect: setSelectedAgent,
      },
    }))
    const builtEdges = buildEdges(agentsWithSelect, setSelectedAgent)
    setNodes(builtNodes)
    setEdges(builtEdges)
  }, [agentsWithSelect, containerWidth, setSelectedAgent])

  // fitView after initial load
  useEffect(() => {
    if (nodes.length > 0 && !didFitRef.current) {
      setTimeout(() => {
        fitView({ padding: 0.2, duration: 300 })
        didFitRef.current = true
      }, 100)
    }
  }, [nodes, fitView])

  const handleNodeClick = useCallback((event, node) => {
    if (node.data?.agent) {
      setSelectedAgent(node.data.agent)
    }
  }, [setSelectedAgent])

  const handleFitView = useCallback(() => {
    fitView({ padding: 0.2, duration: 300 })
  }, [fitView])

  return (
    <div ref={containerRef} style={{ width: '100%', height: '100%', position: 'relative' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView={false}
        attributionPosition="bottom-right"
        proOptions={{ hideAttribution: true }}
        style={{ background: '#0b0d10' }}
        minZoom={0.3}
        maxZoom={2}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={24}
          size={1}
          color="rgba(255,255,255,0.04)"
        />
        <Controls
          style={{
            background: 'rgba(255,255,255,0.04)',
            border: '1px solid rgba(255,255,255,0.07)',
            borderRadius: 6,
          }}
        />
      </ReactFlow>

      {/* Fit view button */}
      <button
        onClick={handleFitView}
        style={{
          position: 'absolute',
          top: 10,
          right: 10,
          background: 'rgba(255,255,255,0.04)',
          border: '1px solid rgba(255,255,255,0.07)',
          borderRadius: 4,
          color: 'rgba(255,255,255,0.4)',
          fontSize: 10,
          padding: '4px 9px',
          cursor: 'pointer',
          fontFamily: 'inherit',
          zIndex: 5,
          transition: 'color 0.15s, background 0.15s',
        }}
        onMouseEnter={e => {
          e.currentTarget.style.background = 'rgba(255,255,255,0.07)'
          e.currentTarget.style.color = '#e2e4e8'
        }}
        onMouseLeave={e => {
          e.currentTarget.style.background = 'rgba(255,255,255,0.04)'
          e.currentTarget.style.color = 'rgba(255,255,255,0.4)'
        }}
      >
        Fit
      </button>

      {/* Empty state overlay */}
      {agents.length === 0 && (
        <div style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 8,
          pointerEvents: 'none',
        }}>
          <div style={{
            fontSize: 11,
            color: 'rgba(255,255,255,0.15)',
            textAlign: 'center',
          }}>
            No agents connected
          </div>
          <div style={{ fontSize: 9, color: 'rgba(255,255,255,0.08)' }}>
            Register an agent to visualize the network
          </div>
        </div>
      )}
    </div>
  )
}

export default function CenterGraph() {
  return (
    <div style={{ flex: 1, height: '100%', background: '#0b0d10', position: 'relative', minWidth: 0 }}>
      <ReactFlowProvider>
        <GraphInner />
      </ReactFlowProvider>
    </div>
  )
}
