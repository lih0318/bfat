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
        try {
          const data = JSON.parse(e.data)
          setStatus(data)
        } catch {
          // ignore
        }
      }
      ws.onclose = () => {
        if (cancelled) return
        ws = null
        failures++
        if (failures >= MAX_FAILURES) return
        const delay = Math.min(3000 * Math.pow(2, failures - 1), 30000)
        reconnectTimer = setTimeout(connect, delay)
      }
      ws.onerror = () => { /* avoid uncaught in console; reconnect handled in onclose */ }
    }
    connect()
    return () => {
      cancelled = true
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      if (ws) {
        ws.onclose = null
        ws.onerror = null
        ws.close()
        ws = null
      }
    }
  }, [wsUrl])

  useEffect(() => {
    if (!accessToken) return
    let cancelled = false
    async function fetch_() {
      const res = await apiFetch('/api/insight', { token: accessToken })
      if (cancelled) return
      if (res.ok) {
        const d: InsightData = await res.json()
        setRegime(d.regime ?? null)
      }
    }
    fetch_()
    const interval = setInterval(fetch_, 30000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [accessToken])

  useEffect(() => {
    if (!accessToken) return
    let cancelled = false
    function extractEquity(d: unknown): number | null {
      if (d && typeof d === 'object' && 'equity' in d && typeof (d as { equity: unknown }).equity === 'number') {
        return (d as { equity: number }).equity
      }
      if (d && typeof d === 'object' && ('totalMarginBalance' in d || 'totalWalletBalance' in d)) {
        const obj = d as Record<string, unknown>
        for (const k of ['totalMarginBalance', 'totalWalletBalance']) {
          const v = obj[k]
          if (v != null && v !== '') {
            const f = typeof v === 'number' ? v : parseFloat(String(v))
            if (!isNaN(f)) return f
          }
        }
      }
      return null
    }
    async function fetchEquity() {
      const res = await apiFetch('/api/equity', { token: accessToken })
      if (cancelled || !res.ok) return
      try {
        const d = await res.json()
        const eq = extractEquity(d)
        if (eq != null) setEquityFallback(eq)
      } catch {
        // ignore
      }
    }
    fetchEquity()
    const interval = setInterval(fetchEquity, 30000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [accessToken])

  const engineState = status?.engine_state ?? 'stopped'
  const isRunning = engineState !== 'stopped'
  const displayState = isRunning ? 'RUNNING' : 'STOPPED'

  const handleStart = async () => {
    setControlError(null)
    setStartLoading(true)
    try {
      const res = await apiFetch('/api/start', {
        method: 'POST',
        token: accessToken,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        const d = err.detail
        const msg = typeof d === 'string' ? d : (Array.isArray(d) && d[0]?.msg ? d[0].msg : null) ?? 'Start failed'
        setControlError(msg)
      }
    } catch (e) {
      setControlError(e instanceof Error ? e.message : 'Start failed')
    } finally {
      setStartLoading(false)
    }
  }

  const handleStop = async () => {
    setControlError(null)
    setStopLoading(true)
    try {
      const res = await apiFetch('/api/stop', {
        method: 'POST',
        token: accessToken,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        const d = err.detail
        const msg = typeof d === 'string' ? d : (Array.isArray(d) && d[0]?.msg ? d[0].msg : null) ?? 'Stop failed'
        setControlError(msg)
      }
    } catch (e) {
      setControlError(e instanceof Error ? e.message : 'Stop failed')
    } finally {
      setStopLoading(false)
    }
  }

  const regimeColors: Record<string, string> = {
    Trending: 'bg-[var(--positive)]/20 text-[var(--positive)]',
    Ranging: 'bg-[var(--accent)]/20 text-[var(--accent)]',
    'High Volatility': 'bg-[var(--negative)]/20 text-[var(--negative)]',
  }
  const regimeClass = regime ? regimeColors[regime] ?? 'bg-[var(--border)]/30 text-[var(--text-muted)]' : 'bg-[var(--border)]/30 text-[var(--text-muted)]'

  const pos = status?.position as PositionData | null
  const rMultiple: number | null =
    status?.r_multiple != null ? Number(status.r_multiple) : null
  const rValidationStatus = status?.r_validation_status ?? null

  const rBadgeClasses: Record<string, string> = {
    OK: 'bg-[var(--positive)]/20 text-[var(--positive)]',
    WARNING: 'bg-yellow-500/20 text-yellow-400',
    CRITICAL: 'bg-[var(--negative)]/20 text-[var(--negative)]',
    ANOMALY: 'bg-purple-500/20 text-purple-400',
    CRITICAL_OUTLIER: 'bg-[var(--negative)]/20 text-[var(--negative)]',
  }
  const rBadgeLabels: Record<string, string> = {
    OK: 'R VALIDATED',
    WARNING: 'R CHECK WARNING',
    CRITICAL: 'R CALCULATION ERROR',
    ANOMALY: 'R OUTLIER DETECTED',
    CRITICAL_OUTLIER: 'R CRITICAL OUTLIER',
  }
  const systemHealthClass =
    status?.system_health === 'DEGRADED'
      ? 'bg-amber-500/20 text-amber-400'
      : 'bg-[var(--positive)]/20 text-[var(--positive)]'

  const displayEquity =
    typeof status?.equity === 'number'
      ? status.equity
      : typeof equityFallback === 'number'
        ? equityFallback
        : null

  return (
    <div className="min-h-screen bg-[#0a0e12] text-[var(--text)]">
      <header className="sticky top-0 z-10 border-b border-[var(--border)] bg-[var(--bg-card)]/95 backdrop-blur px-4 py-3 md:px-6 shadow-[var(--shadow)]">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold text-[var(--accent)]">BFAT</h1>
            <div
              className={`h-2.5 w-2.5 rounded-full flex-shrink-0 ${
                isRunning ? 'bg-[var(--positive)]' : 'bg-[var(--text-muted)]'
              }`}
              title={displayState}
            />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {regime && (
              <span className={`rounded-lg px-3 py-1.5 text-sm font-medium ${regimeClass}`}>
                {regime}
              </span>
            )}
            {rValidationStatus != null && (
              <span
                className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
                  rBadgeClasses[rValidationStatus] ?? 'bg-[var(--border)]/30 text-[var(--text-muted)]'
                }`}
              >
                {rBadgeLabels[rValidationStatus] ?? rValidationStatus}
              </span>
            )}
            <span
              className={`rounded-lg px-3 py-1.5 text-xs font-medium ${systemHealthClass}`}
              title={status?.system_health ?? 'HEALTHY'}
            >
              {status?.system_health === 'DEGRADED' ? 'DEGRADED' : 'HEALTHY'}
            </span>
            <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-elevated)] px-4 py-2" title={displayEquity === null ? '잔고를 불러오지 못했습니다. API 키를 확인하세요.' : undefined}>
              <span className="text-xs text-[var(--text-muted)]">Equity</span>
              <p className="font-medium">{displayEquity !== null ? displayEquity.toFixed(2) : '–'} USDT</p>
              {displayEquity === null && (
                <p className="text-[10px] text-[var(--text-muted)] mt-0.5">연결 안 됨</p>
              )}
            </div>
            {status?.kill_switch_triggered && (
              <div className="rounded-xl bg-[var(--negative)]/20 px-4 py-2 text-[var(--negative)] font-semibold">
                KILL SWITCH
              </div>
            )}
            {status?.error && (
              <div className="rounded-xl bg-[var(--negative)]/20 px-4 py-2 text-[var(--negative)]">
                CRITICAL
              </div>
            )}
            <div className="relative ml-2">
              <button
                onClick={() => setUserOpen((o) => !o)}
                className="flex min-h-[44px] items-center gap-2 rounded-xl border border-[var(--border)] bg-[var(--bg-elevated)] px-3 py-2 transition-all duration-200 touch-manipulation hover:bg-[var(--border)]/30 focus:outline-none focus:ring-2 focus:ring-[var(--accent)] focus:ring-offset-2 focus:ring-offset-[#0a0e12]"
              >
                <span className="text-sm font-medium">{username ?? 'User'}</span>
                <svg className="h-4 w-4 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              {userOpen && (
                <>
                  <div
                    className="fixed inset-0 z-10"
                    onClick={() => setUserOpen(false)}
                  />
                  <div className="absolute right-0 top-full z-20 mt-1 w-40 rounded-xl border border-[var(--border)] bg-[var(--bg-card)] py-1 shadow-lg">
                    <button
                      onClick={() => {
                        setUserOpen(false)
                        logout()
                      }}
                      className="w-full px-4 py-2 text-left text-sm transition-colors hover:bg-[var(--bg-elevated)] touch-manipulation focus:outline-none focus:bg-[var(--bg-elevated)]"
                    >
                      Logout
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </header>

      <nav className="border-b border-[var(--border)] bg-[var(--bg-elevated)]">
        <div className="mx-auto flex max-w-6xl gap-0">
          {(['dashboard', 'insight', 'trades', 'logs', 'chart'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`relative min-h-[48px] flex-1 px-4 font-medium capitalize transition-all duration-200 touch-manipulation md:flex-none md:px-6 ${
                activeTab === tab
                  ? 'text-[var(--accent)]'
                  : 'text-[var(--text-muted)] hover:text-[var(--text)]'
              }`}
            >
              {tab}
              {activeTab === tab && (
                <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-[var(--accent)] transition-opacity" />
              )}
            </button>
          ))}
        </div>
      </nav>

      <main className="mx-auto max-w-6xl flex-1 p-4 md:p-6">
        {activeTab === 'dashboard' && (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            <div className="md:col-span-2 lg:col-span-3">
              <ControlPanel
                engineState={engineState}
                startLoading={startLoading}
                stopLoading={stopLoading}
                controlError={controlError}
                onStart={handleStart}
                onStop={handleStop}
              />
            </div>
            <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5 shadow-[var(--shadow)] ring-1 ring-white/5 backdrop-blur-sm">
              <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                Equity
              </h3>
              <p className="text-2xl font-semibold">{displayEquity !== null ? displayEquity.toFixed(2) : '–'} USDT</p>
              {displayEquity === null && (
                <p className="mt-2 text-xs text-[var(--text-muted)]">잔고를 불러오지 못했습니다.</p>
              )}
            </div>
            <div className="lg:col-span-2">
              <PositionCard
                position={pos ?? null}
                currentStopPrice={status?.current_stop_price ?? null}
                takeProfit={status?.take_profit ?? null}
                rMultiple={rMultiple}
              />
            </div>
            <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5 shadow-[var(--shadow)] ring-1 ring-white/5 backdrop-blur-sm">
              <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                Last Signal
              </h3>
              {status?.last_signal ? (
                <div className="space-y-2">
                  <p><span className="text-[var(--text-muted)]">Symbol:</span> {status.last_signal.symbol}</p>
                  <p><span className="text-[var(--text-muted)]">Side:</span> {status.last_signal.side}</p>
                  <p className="text-xs text-[var(--text-muted)]">{status.last_signal.signal_candle_ts || status.last_signal.signal_time}</p>
                </div>
              ) : (
                <p className="text-[var(--text-muted)]">No signal</p>
              )}
            </div>
          </div>
        )}

        {activeTab === 'insight' && (
          <div className="space-y-4">
            <InsightTab />
          </div>
        )}

        {activeTab === 'trades' && (
          <div className="space-y-4">
            <TradesTab />
          </div>
        )}

        {activeTab === 'logs' && (
          <div className="space-y-4">
            <LogsPanel />
          </div>
        )}

        {activeTab === 'chart' && (
          <div className="space-y-4">
            <ChartTab />
          </div>
        )}
      </main>
    </div>
  )
}
