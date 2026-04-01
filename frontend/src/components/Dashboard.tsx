import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { useAuth } from '../context/AuthContext'
import { ChartTab } from './ChartTab'
import { ControlPanel } from './ControlPanel'
import { InsightTab } from './InsightTab'
import { LogsPanel } from './LogsPanel'
import { PositionCard, type PositionData } from './PositionCard'
import { TradesTab } from './TradesTab'

interface StatusData {
  engine_state: string
  position: Record<string, unknown> | null
  last_signal: Record<string, string> | null
  current_stop_price: number | null
  take_profit: number | null
  r_multiple: number | null
  r_validation_status: string | null
  system_health: string
  equity: number
  kill_switch_triggered: boolean
  error: string | null
}

interface InsightData {
  regime: string
}

type TabId = 'dashboard' | 'insight' | 'trades' | 'logs' | 'chart'

const TAB_LIST: { id: TabId; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'insight', label: 'Insight' },
  { id: 'trades', label: 'Trades' },
  { id: 'logs', label: 'Logs' },
  { id: 'chart', label: 'Chart' },
]

export function Dashboard() {
  const { accessToken, logout, username } = useAuth()
  const [status, setStatus] = useState<StatusData | null>(null)
  const [equityFallback, setEquityFallback] = useState<number | null>(null)
  const [activeTab, setActiveTab] = useState<TabId>('dashboard')
  const [userOpen, setUserOpen] = useState(false)
  const [regime, setRegime] = useState<string | null>(null)
  const [startLoading, setStartLoading] = useState(false)
  const [stopLoading, setStopLoading] = useState(false)
  const [controlError, setControlError] = useState<string | null>(null)

  const wsUrl = useCallback(() => {
    const base = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/status`
    return accessToken ? `${base}?token=${encodeURIComponent(accessToken)}` : null
  }, [accessToken])

  useEffect(() => {
    const url = wsUrl()
    if (!url) return
    let cancelled = false
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let failures = 0
    let ws: WebSocket | null = null
    const MAX_FAILURES = 5
    const connect = () => {
      if (cancelled) return
      ws = new WebSocket(url)
      ws.onopen = () => { failures = 0 }
      ws.onmessage = (e) => {
        try { setStatus(JSON.parse(e.data)) } catch { /* */ }
      }
      ws.onclose = () => {
        if (cancelled) return
        ws = null
        failures++
        if (failures >= MAX_FAILURES) return
        const delay = Math.min(3000 * Math.pow(2, failures - 1), 30000)
        reconnectTimer = setTimeout(connect, delay)
      }
      ws.onerror = () => {}
    }
    connect()
    return () => {
      cancelled = true
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
      if (ws) { ws.onclose = null; ws.onerror = null; ws.close(); ws = null }
    }
  }, [wsUrl])

  useEffect(() => {
    if (!accessToken) return
    let cancelled = false
    async function fetch_() {
      const res = await apiFetch('/api/insight', { token: accessToken })
      if (cancelled) return
      if (res.ok) { const d: InsightData = await res.json(); setRegime(d.regime ?? null) }
    }
    fetch_()
    const interval = setInterval(fetch_, 30000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [accessToken])

  useEffect(() => {
    if (!accessToken) return
    let cancelled = false
    function extractEquity(d: unknown): number | null {
      if (d && typeof d === 'object' && 'equity' in d && typeof (d as { equity: unknown }).equity === 'number') return (d as { equity: number }).equity
      if (d && typeof d === 'object' && ('totalMarginBalance' in d || 'totalWalletBalance' in d)) {
        const obj = d as Record<string, unknown>
        for (const k of ['totalMarginBalance', 'totalWalletBalance']) {
          const v = obj[k]
          if (v != null && v !== '') { const f = typeof v === 'number' ? v : parseFloat(String(v)); if (!isNaN(f)) return f }
        }
      }
      return null
    }
    async function fetchEquity() {
      const res = await apiFetch('/api/equity', { token: accessToken })
      if (cancelled || !res.ok) return
      try { const d = await res.json(); const eq = extractEquity(d); if (eq != null) setEquityFallback(eq) } catch { /* */ }
    }
    fetchEquity()
    const interval = setInterval(fetchEquity, 30000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [accessToken])

  const engineState = status?.engine_state ?? 'stopped'
  const isRunning = engineState !== 'stopped'

  const handleStart = async () => {
    setControlError(null); setStartLoading(true)
    try {
      const res = await apiFetch('/api/start', { method: 'POST', token: accessToken })
      if (!res.ok) { const err = await res.json().catch(() => ({})); const d = err.detail; setControlError(typeof d === 'string' ? d : (Array.isArray(d) && d[0]?.msg ? d[0].msg : null) ?? 'Start failed') }
    } catch (e) { setControlError(e instanceof Error ? e.message : 'Start failed') }
    finally { setStartLoading(false) }
  }

  const handleStop = async () => {
    setControlError(null); setStopLoading(true)
    try {
      const res = await apiFetch('/api/stop', { method: 'POST', token: accessToken })
      if (!res.ok) { const err = await res.json().catch(() => ({})); const d = err.detail; setControlError(typeof d === 'string' ? d : (Array.isArray(d) && d[0]?.msg ? d[0].msg : null) ?? 'Stop failed') }
    } catch (e) { setControlError(e instanceof Error ? e.message : 'Stop failed') }
    finally { setStopLoading(false) }
  }

  const pos = status?.position as PositionData | null
  const rMultiple: number | null = status?.r_multiple != null ? Number(status.r_multiple) : null

  const displayEquity = typeof status?.equity === 'number' ? status.equity : typeof equityFallback === 'number' ? equityFallback : null

  return (
    <div className="min-h-screen bg-[var(--bg-base)] text-[var(--text)]">

      {/* ─── Header ─── */}
      <header className="sticky top-0 z-30 border-b border-[var(--border-subtle)] bg-[var(--bg-card)]/95 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 md:px-6">

          {/* Left: Brand + Status */}
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2.5">
              <span className="text-lg font-bold tracking-tight text-[var(--accent)]">BFAT</span>
              <div className={`h-2 w-2 rounded-full ${isRunning ? 'bg-[var(--positive)] animate-pulse-glow' : 'bg-[var(--text-muted)]'}`} />
              <span className="text-[10px] font-semibold uppercase tracking-widest text-[var(--text-muted)]">
                {isRunning ? 'LIVE' : 'OFF'}
              </span>
            </div>

            {regime && (
              <span className={`badge ${
                regime.toUpperCase() === 'TRENDING'
                  ? 'bg-[var(--positive-muted)] text-[var(--positive)]'
                  : regime.toUpperCase() === 'RANGING'
                    ? 'bg-[var(--accent-muted)] text-[var(--accent)]'
                    : 'bg-[var(--border)]/30 text-[var(--text-muted)]'
              }`}>
                {regime}
              </span>
            )}

            {status?.system_health === 'DEGRADED' && (
              <span className="badge bg-[var(--warning-muted)] text-[var(--warning)]">DEGRADED</span>
            )}
          </div>

          {/* Right: Equity + Alerts + User */}
          <div className="flex items-center gap-3">
            {/* Equity Pill */}
            <div className="hidden sm:flex items-center gap-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-4 py-2">
              <span className="text-[10px] font-medium uppercase tracking-wider text-[var(--text-muted)]">Equity</span>
              <span className="text-sm font-semibold tabular-nums">
                {displayEquity !== null ? displayEquity.toFixed(2) : '–'}
                <span className="ml-1 text-[10px] font-normal text-[var(--text-muted)]">USDT</span>
              </span>
            </div>

            {status?.kill_switch_triggered && (
              <span className="badge bg-[var(--negative-muted)] text-[var(--negative)]">KILL SWITCH</span>
            )}
            {status?.error && (
              <span className="badge bg-[var(--negative-muted)] text-[var(--negative)]">CRITICAL</span>
            )}

            {/* User Menu */}
            <div className="relative">
              <button
                onClick={() => setUserOpen((o) => !o)}
                className="flex min-h-[40px] items-center gap-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-3 py-2 transition-colors hover:border-[var(--border)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              >
                <div className="flex h-6 w-6 items-center justify-center rounded-full bg-[var(--accent-muted)] text-xs font-bold text-[var(--accent)]">
                  {(username ?? 'U')[0].toUpperCase()}
                </div>
                <span className="hidden text-sm font-medium sm:inline">{username ?? 'User'}</span>
                <svg className="h-3.5 w-3.5 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              {userOpen && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setUserOpen(false)} />
                  <div className="absolute right-0 top-full z-20 mt-2 w-44 rounded-xl border border-[var(--border)] bg-[var(--bg-card)] py-1 shadow-[var(--shadow-lg)] animate-fade-in">
                    <div className="px-4 py-2 text-xs text-[var(--text-muted)]">{username}</div>
                    <div className="mx-2 border-t border-[var(--border-subtle)]" />
                    <button
                      onClick={() => { setUserOpen(false); logout() }}
                      className="w-full px-4 py-2.5 text-left text-sm transition-colors hover:bg-[var(--bg-elevated)] focus:outline-none"
                    >
                      로그아웃
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </header>

      {/* ─── Tab Navigation ─── */}
      <nav className="border-b border-[var(--border-subtle)] bg-[var(--bg-base)]">
        <div className="mx-auto flex max-w-7xl gap-0 px-2 md:px-4">
          {TAB_LIST.map(({ id, label }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`relative px-4 py-3 text-sm font-medium transition-colors md:px-5 ${
                activeTab === id
                  ? 'text-[var(--accent)]'
                  : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]'
              }`}
            >
              {label}
              {activeTab === id && (
                <span className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-[var(--accent)]" />
              )}
            </button>
          ))}
        </div>
      </nav>

      {/* ─── Main Content ─── */}
      <main className="mx-auto max-w-7xl flex-1 p-4 md:p-6">
        {activeTab === 'dashboard' && (
          <div className="animate-fade-in space-y-5">
            <ControlPanel
              engineState={engineState}
              startLoading={startLoading}
              stopLoading={stopLoading}
              controlError={controlError}
              onStart={handleStart}
              onStop={handleStop}
            />

            <div className="grid gap-5 lg:grid-cols-3">
              {/* Equity Card */}
              <div className="card p-5">
                <p className="section-title mb-3">Equity</p>
                <p className="text-2xl font-bold tabular-nums">
                  {displayEquity !== null ? displayEquity.toFixed(2) : '–'}
                  <span className="ml-1.5 text-sm font-normal text-[var(--text-muted)]">USDT</span>
                </p>
                {displayEquity === null && (
                  <p className="mt-2 text-xs text-[var(--text-muted)]">잔고를 불러오지 못했습니다.</p>
                )}
              </div>

              {/* Position Card */}
              <div className="lg:col-span-2">
                <PositionCard
                  position={pos ?? null}
                  currentStopPrice={status?.current_stop_price ?? null}
                  takeProfit={status?.take_profit ?? null}
                  rMultiple={rMultiple}
                />
              </div>
            </div>

            {/* Last Signal */}
            <div className="card p-5">
              <p className="section-title mb-3">Last Signal</p>
              {status?.last_signal ? (
                <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
                  <span><span className="text-[var(--text-muted)]">Symbol</span> <span className="font-medium">{status.last_signal.symbol}</span></span>
                  <span><span className="text-[var(--text-muted)]">Side</span> <span className="font-medium">{status.last_signal.side}</span></span>
                  <span className="text-xs text-[var(--text-muted)]">{status.last_signal.signal_candle_ts || status.last_signal.signal_time}</span>
                </div>
              ) : (
                <p className="text-sm text-[var(--text-muted)]">No signal</p>
              )}
            </div>
          </div>
        )}

        {activeTab === 'insight' && <div className="animate-fade-in"><InsightTab /></div>}
        {activeTab === 'trades' && <div className="animate-fade-in"><TradesTab /></div>}
        {activeTab === 'logs' && <div className="animate-fade-in"><LogsPanel /></div>}
        {activeTab === 'chart' && <div className="animate-fade-in"><ChartTab /></div>}
      </main>
    </div>
  )
}
