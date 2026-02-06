import { useState } from 'react'
import { Layout, type TabId } from './components/Layout'
import { WalletTab } from './tabs/WalletTab'
import { ChartsTab } from './tabs/ChartsTab'
import { PositionsTab } from './tabs/PositionsTab'
import { AutopilotTab } from './tabs/AutopilotTab'
import { JournalTab } from './tabs/JournalTab'
import './App.css'

function App() {
  const [activeTab, setActiveTab] = useState<TabId>('wallet')

  const renderTab = () => {
    switch (activeTab) {
      case 'wallet':
        return <WalletTab />
      case 'charts':
        return <ChartsTab />
      case 'positions':
        return <PositionsTab />
      case 'autopilot':
        return <AutopilotTab />
      case 'journal':
        return <JournalTab />
      default:
        return <WalletTab />
    }
  }

  return (
    <Layout activeTab={activeTab} onTab={setActiveTab}>
      {renderTab()}
    </Layout>
  )
}

export default App
