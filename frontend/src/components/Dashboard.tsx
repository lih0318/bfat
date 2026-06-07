import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { useAuth } from '../context/AuthContext'
import { ChartTab } from './ChartTab'
import { ControlPanel, type StrategyConfig, type StrategyMode } from './ControlPanel'
import { InsightTab } from './InsightTab'
import { LogsPanel } from './LogsPanel'
import { PositionCard, type PositionData } from './PositionCard'
import { TradesTab } from './TradesTab'

interface StreamDiag {
  connected: boolean
  last_message_ts: number
  last_disconnect_ts: number
  last_error: string
  reconnect_count: number
  current_backoff: number
  candle_buffer_size?: number
}

interface Diagnostics {
  market_stream: StreamDiag | null
  user_stream: StreamDiag | null
  last_insight_update_ts: number | null
  insight_age_seconds: number | null
  insight_stale: boolean
}

interface StatusData {
  engine_state: string
  symbols?: string[]
  max_concurrent_positions?: number
  open_position_count?: number
  positions?: PositionData[]
  symbol_statuses?: SymbolStatusData[]
  position: PositionData | null
  last_signal: Record<string, string> | null
  current_stop_price: number | null
  take_profit: number | null
  tp_protection_mode: 'exchange' | 'fallback' | 'none' | 'failed' | 'repriced'
  tp_verified: boolean | null
  tp_status: string | null
  tp_error: string | null
  sl_protection_mode: 'exchange' | 'recovering' | 'none'
  sl_verified: boolean | null
  r_multiple: number | null
  r_validation_status: string | null
  system_health: string
  equity: number
  unrealized_pnl: number | null
  total_realized_pnl: number | null
  kill_switch_triggered: boolean
  post_close_cooldown: number
  error: string | null
  diagnostics?: Diagnostics
}

interface SymbolStatusData {
  symbol: string
  engine_state: string
  position: PositionData | null
  current_stop_price: number | null
  take_profit: number | null
  tp_protection_mode: 'exchange' | 'fallback' | 'none' | 'failed' | 'repriced'
  tp_verified: boolean | null
  tp_status: string | null
  tp_error: string | null
  sl_protection_mode: 'exchange' | 'recovering' | 'none'
  sl_verified: boolean | null
  r_multiple: number | null
  r_validation_status: string | null
  unrealized_pnl: number | null
  total_realized_pnl: number | null
  kill_switch_triggered: boolean
  post_close_cooldown: number
}

interface InsightData {
  regime: string
  regime_classifier?: {
    trend_direction?: 'up' | 'down' | 'neutral'
    trend_strength?: 'weak' | 'moderate' | 'strong' | 'neutral'
  }
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
  const [trendDirection, setTrendDirection] = useState<'up' | 'down' | 'neutral' | null>(null)
  const [trendStrength, setTrendStrength] = useState<'weak' | 'moderate' | 'strong' | 'neutral' | null>(null)
  const [startLoading, setStartLoading] = useState(false)
  const [stopLoading, setStopLoading] = useState(false)
  const [controlError, setControlError] = useState<string | null>(null)
  const [strategyConfig, setStrategyConfig] = useState<StrategyConfig | null>(null)
  const [strategyLoading, setStrategyLoading] = useState(false)
  const [strategyError, setStrategyError] = useState<string | null>(null)

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
      if (res.ok) { const d: InsightData = await res.json(); setRegime(d.regime ?? null); setTrendDirection(d.regime_classifier?.trend_direction ?? null); setTrendStrength(d.regime_classifier?.trend_strength ?? null) }
    }
    fetch_()
    const interval = setInterval(fetch_, 15000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [accessToken])

  const fetchStrategyConfig = useCallback(async () => {
    if (!accessToken) return
    try {
      const res = await apiFetch('/api/strategy/config', { token: accessToken })
      if (!res.ok) return
      const data: StrategyConfig = await res.json()
      setStrategyConfig(data)
      setStrategyError(null)
    } catch {
      setStrategyError('Strategy config unavailable')
    }
  }, [accessToken])

  useEffect(() => {
    fetchStrategyConfig()
  }, [fetchStrategyConfig])

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
      else fetchStrategyConfig()
    } catch (e) { setControlError(e instanceof Error ? e.message : 'Start failed') }
    finally { setStartLoading(false) }
  }

  const handleStop = async () => {
    setControlError(null); setStopLoading(true)
    try {
      const res = await apiFetch('/api/stop', { method: 'POST', token: accessToken })
      if (!res.ok) { const err = await res.json().catch(() => ({})); const d = err.detail; setControlError(typeof d === 'string' ? d : (Array.isArray(d) && d[0]?.msg ? d[0].msg : null) ?? 'Stop failed') }
      else fetchStrategyConfig()
    } catch (e) { setControlError(e instanceof Error ? e.message : 'Stop failed') }
    finally { setStopLoading(false) }
  }

  const handleStrategyModeChange = async (mode: StrategyMode) => {
    if (!accessToken) return
    setStrategyError(null)
    setStrategyLoading(true)
    try {
      const res = await apiFetch('/api/strategy/config', {
        method: 'PUT',
        token: accessToken,
        body: JSON.stringify({ mode }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        const detail = err.detail
        setStrategyError(typeof detail === 'string' ? detail : 'Strategy update failed')
        return
      }
      const data: StrategyConfig = await res.json()
      setStrategyConfig(data)
    } catch (e) {
      setStrategyError(e instanceof Error ? e.message : 'Strategy update failed')
    } finally {
      setStrategyLoading(false)
    }
  }

  const pos = status?.position ?? null
  const symbols = status?.symbols ?? strategyConfig?.symbols ?? []
  const symbolStatuses = status?.symbol_statuses ?? []
  const openSymbolStatuses = symbolStatuses.filter((s) => s.position)
  const positionCards = openSymbolStatuses.length > 0
    ? openSymbolStatuses
    : [{
        symbol: pos?.symbol ?? symbols[0] ?? 'BTCUSDT',
        engine_state: engineState,
        position: pos,
        current_stop_price: status?.current_stop_price ?? null,
        take_profit: status?.take_profit ?? null,
        tp_protection_mode: status?.tp_protection_mode ?? 'none',
        tp_verified: status?.tp_verified ?? null,
        tp_status: status?.tp_status ?? null,
        tp_error: status?.tp_error ?? null,
        sl_protection_mode: status?.sl_protection_mode ?? 'none',
        sl_verified: status?.sl_verified ?? null,
        r_multiple: status?.r_multiple ?? null,
        r_validation_status: status?.r_validation_status ?? null,
        unrealized_pnl: status?.unrealized_pnl ?? null,
        total_realized_pnl: status?.total_realized_pnl ?? null,
        kill_switch_triggered: status?.kill_switch_triggered ?? false,
        post_close_cooldown: status?.post_close_cooldown ?? 0,
      }]
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

            {trendDirection && trendDirection !== 'neutral' && (() => {
              const isUp = trendDirection === 'up'
              const s = trendStrength ?? 'moderate'
              let arrow: string
              let label: string
              let opacity = ''
              if (s === 'strong') {
                arrow = isUp ? '▲' : '▼'
                label = isUp ? '강한 Up' : '강한 Down'
              } else if (s === 'moderate') {
                arrow = isUp ? '▲' : '▼'
                label = isUp ? 'Up' : 'Down'
              } else {
                arrow = isUp ? '↗' : '↘'
                label = isUp ? 'Up 경향' : 'Down 경향'
                opacity = 'opacity-75'
              }
              return (
                <span className={`badge ${opacity} ${
                  isUp
                    ? 'bg-[var(--positive-muted)] text-[var(--positive)]'
                    : 'bg-[var(--negative-muted)] text-[var(--negative)]'
                }`}>
                  {arrow} {label}
                </span>
              )
            })()}

            {status?.post_close_cooldown != null && status.post_close_cooldown > 0 && (
              <span className="badge bg-[var(--accent-muted)] text-[var(--accent)]">
                COOLDOWN {status.post_close_cooldown}
              </span>
            )}

            {status?.system_health === 'DEGRADED' && (
              <span className="badge bg-[var(--warning-muted)] text-[var(--warning)]">DEGRADED</span>
            )}

            {isRunning && status?.diagnostics?.market_stream && !status.diagnostics.market_stream.connected && (
              <span className="badge bg-[var(--negative-muted)] text-[var(--negative)]">STREAM OFFLINE</span>
            )}

            {isRunning && status?.diagnostics?.market_stream?.connected && status?.diagnostics?.market_stream?.reconnect_count > 0 && (
              <span className="badge bg-[var(--warning-muted)] text-[var(--warning)]">
                RECONNECTED x{status.diagnostics.market_stream.reconnect_count}
              </span>
            )}

            {isRunning && status?.diagnostics?.insight_stale && (
              <span className="badge bg-[var(--warning-muted)] text-[var(--warning)]">INSIGHT STALE</span>
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
            {status?.error && (
              <div className="rounded-xl border border-[var(--negative)]/30 bg-[var(--negative-muted)] p-4">
                <div className="flex items-center gap-2">
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--negative)]/20 text-xs font-bold text-[var(--negative)]">!</span>
                  <p className="text-sm font-semibold text-[var(--negative)]">Critical Error</p>
                </div>
                <p className="mt-1.5 text-sm text-[var(--negative)]/80">{status.error}</p>
              </div>
            )}
            <ControlPanel
              engineState={engineState}
              startLoading={startLoading}
              stopLoading={stopLoading}
              controlError={controlError}
              strategyConfig={strategyConfig}
              strategyLoading={strategyLoading}
              strategyError={strategyError}
              symbols={symbols}
              openPositionCount={status?.open_position_count ?? openSymbolStatuses.length}
              maxConcurrentPositions={status?.max_concurrent_positions ?? strategyConfig?.max_concurrent_positions}
              onStart={handleStart}
              onStop={handleStop}
              onStrategyModeChange={handleStrategyModeChange}
            />

            <div className="grid gap-5 lg:grid-cols-3">
              {/* Equity & PnL Card */}
              <div className="card p-5">
                <p className="section-title mb-3">Equity</p>
                <p className="text-2xl font-bold tabular-nums">
                  {displayEquity !== null ? displayEquity.toFixed(2) : '–'}
                  <span className="ml-1.5 text-sm font-normal text-[var(--text-muted)]">USDT</span>
                </p>
                {displayEquity === null && (
                  <p className="mt-2 text-xs text-[var(--text-muted)]">잔고를 불러오지 못했습니다.</p>
                )}

                {/* Unrealized PnL */}
                <div className="mt-4 border-t border-[var(--border-subtle)] pt-3">
                  <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">미실현 손익</p>
                  {status?.unrealized_pnl != null ? (
                    <p className={`mt-0.5 text-lg font-bold tabular-nums ${status.unrealized_pnl >= 0 ? 'text-[var(--positive)]' : 'text-[var(--negative)]'}`}>
                      {status.unrealized_pnl >= 0 ? '+' : ''}{status.unrealized_pnl.toFixed(4)}
                      <span className="ml-1 text-xs font-normal text-[var(--text-muted)]">USDT</span>
                    </p>
                  ) : (
                    <p className="mt-0.5 text-sm text-[var(--text-muted)]">–</p>
                  )}
                </div>

                {/* Total Realized PnL */}
                <div className="mt-3 border-t border-[var(--border-subtle)] pt-3">
                  <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">총 실현손익</p>
                  {status?.total_realized_pnl != null ? (
                    <p className={`mt-0.5 text-lg font-bold tabular-nums ${status.total_realized_pnl >= 0 ? 'text-[var(--positive)]' : 'text-[var(--negative)]'}`}>
                      {status.total_realized_pnl >= 0 ? '+' : ''}{status.total_realized_pnl.toFixed(4)}
                      <span className="ml-1 text-xs font-normal text-[var(--text-muted)]">USDT</span>
                    </p>
                  ) : (
                    <p className="mt-0.5 text-sm text-[var(--text-muted)]">–</p>
                  )}
                </div>
              </div>

              {/* Position Card */}
              <div className="space-y-4 lg:col-span-2">
                {positionCards.map((item) => (
                  <PositionCard
                    key={item.position?.correlation_id ?? item.symbol}
                    position={item.position ?? null}
                    currentStopPrice={item.current_stop_price ?? null}
                    takeProfit={item.take_profit ?? null}
                    tpProtectionMode={item.tp_protection_mode ?? 'none'}
                    slProtectionMode={item.sl_protection_mode ?? 'none'}
                    tpError={item.tp_error ?? null}
                    rMultiple={item.r_multiple != null ? Number(item.r_multiple) : rMultiple}
                  />
                ))}
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
