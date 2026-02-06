import { type ReactNode } from 'react'
import './Layout.css'

type TabId = 'wallet' | 'charts' | 'positions' | 'autopilot' | 'journal'

interface LayoutProps {
  activeTab: TabId
  onTab: (tab: TabId) => void
  children: ReactNode
}

const TABS: { id: TabId; label: string }[] = [
  { id: 'wallet', label: 'Wallet' },
  { id: 'charts', label: 'Charts' },
  { id: 'positions', label: 'Positions' },
  { id: 'autopilot', label: 'Rich Man' },
  { id: 'journal', label: 'Journal' },
]

export function Layout({ activeTab, onTab, children }: LayoutProps) {
  return (
    <div className="layout">
      <header className="layout-header">
        <h1 className="layout-title">Binance Futures Auto Trader</h1>
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
