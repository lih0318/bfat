import { useEffect, useState } from 'react'
import { Layout, type TabId } from './components/Layout'
import { LoginPage } from './components/LoginPage'
import { WalletTab } from './tabs/WalletTab'
import { ChartsTab } from './tabs/ChartsTab'
import { PositionsTab } from './tabs/PositionsTab'
import { AutopilotTab } from './tabs/AutopilotTab'
import { InsightTab } from './tabs/InsightTab'
import { JournalTab } from './tabs/JournalTab'
import { getAuthToken, clearAuthToken, api } from './api/client'
import './App.css'

function App() {
  const [activeTab, setActiveTab] = useState<TabId>('wallet')
  const [authenticated, setAuthenticated] = useState<boolean | null>(null) // null = checking

  // Check auth on mount
  useEffect(() => {
    const token = getAuthToken()
    if (!token) {
      setAuthenticated(false)
      return
    }
    // Verify token is still valid
    api.auth
      .check()
      .then((res) => {
        setAuthenticated(res.authenticated)
        if (!res.authenticated) clearAuthToken()
      })
      .catch(() => {
        // If server doesn't require auth (no credentials configured), treat as authenticated
        setAuthenticated(true)
      })
  }, [])

  // Listen for auth-expired events from API client
  useEffect(() => {
    const handler = () => setAuthenticated(false)
    window.addEventListener('auth-expired', handler)
    return () => window.removeEventListener('auth-expired', handler)
  }, [])

  const handleLogin = () => {
    setAuthenticated(true)
  }

  const handleLogout = () => {
    api.auth.logout().catch(() => {})
    clearAuthToken()
    setAuthenticated(false)
  }

  // Loading state
  if (authenticated === null) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
        <p style={{ color: 'var(--text-muted)' }}>Loading...</p>
      </div>
    )
  }

  // Not authenticated
  if (!authenticated) {
    return <LoginPage onLogin={handleLogin} />
  }

  // Authenticated — show main app
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
      case 'insight':
        return <InsightTab />
      case 'journal':
        return <JournalTab />
      default:
        return <WalletTab />
    }
  }

  return (
    <Layout activeTab={activeTab} onTab={setActiveTab} onLogout={handleLogout}>
      {renderTab()}
    </Layout>
  )
}

export default App
