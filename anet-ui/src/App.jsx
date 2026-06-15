import React, { useEffect } from 'react'
import { useStore } from './store/useStore'
import TopBar from './components/TopBar'
import LeftPanel from './components/LeftPanel'
import CenterGraph from './components/CenterGraph'
import RightPanel from './components/RightPanel'
import BottomLog from './components/BottomLog'
import AddAgentModal from './components/AddAgentModal'

export default function App() {
  const { fetchAgents, fetchTasks, connectWS, showAddAgent, rightCollapsed } = useStore()

  useEffect(() => {
    connectWS()
    fetchAgents()
    fetchTasks()
    const interval = setInterval(fetchAgents, 5000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="app">
      <TopBar />
      <div className="main-row">
        <LeftPanel />
        <div className="center-col">
          <CenterGraph />
          <BottomLog />
        </div>
        {!rightCollapsed && <RightPanel />}
      </div>
      {showAddAgent && <AddAgentModal />}
    </div>
  )
}
