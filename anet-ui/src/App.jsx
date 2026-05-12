import React, { useEffect } from 'react'
import { useStore } from './store/useStore'
import TopBar from './components/TopBar'
import LeftPanel from './components/LeftPanel'
import CenterGraph from './components/CenterGraph'
import RightPanel from './components/RightPanel'
import BottomLog from './components/BottomLog'

export default function App() {
  const { fetchAgents, fetchTasks, connectWS } = useStore()

  useEffect(() => {
    connectWS()
    fetchAgents()
    fetchTasks()
    const interval = setInterval(fetchAgents, 3000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      <TopBar />
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden', minHeight: 0 }}>
        <LeftPanel />
        <CenterGraph />
        <RightPanel />
      </div>
      <BottomLog />
    </div>
  )
}
