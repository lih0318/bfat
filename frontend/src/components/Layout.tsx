import { type ReactNode } from 'react'
import './Layout.css'

type TabId = 'wallet' | 'charts' | 'positions' | 'autopilot' | 'insight' | 'journal'

interface LayoutProps {
  activeTab: TabId
  onTab: (tab: TabId) => void
  onLogout?: () => void
  children: ReactNode
}

const TABS: { id: TabId; label: string }[] = [
  { id: 'wallet', label: 'Wallet' },
  { id: 'charts', label: 'Charts' },
  { id: 'positions', label: 'Positions' },
  { id: 'autopilot', label: 'Rich Man' },
  { id: 'insight', label: 'Insight' },
  { id: 'journal', label: 'Journal' },
]

export function Layout({ activeTab, onTab, onLogout, children }: LayoutProps) {
  return (
    <div className="layout">
      <header className="layout-header">
        <div className="layout-header-top">
          <h1 className="layout-title">Binance Futures Auto Trader</h1>
          {onLogout && (
            <button type="button" className="layout-logout-btn" onClick={onLogout}>
              Logout
            </button>
          )}
        </div>
        <nav className="layout-tabs">
          {TABS.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              className={`layout-tab ${activeTab === id ? 'active' : ''}`}
              onClick={() => onTab(id)}
            >
              {label}
            </button>
          ))}
        </nav>
      </header>
      <main className="layout-main">{children}</main>
    </div>
  )
}

export type { TabId }
