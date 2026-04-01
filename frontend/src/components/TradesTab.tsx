import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '../api/client'
import { useAuth } from '../context/AuthContext'

interface TradeSummary {
  total_trades: number
  win_rate: number
  average_r: number
  expectancy_r: number
  total_net_pnl: number
  max_drawdown_r: number
  best_trade_r: number
  worst_trade_r: number
}

interface Trade {
  id: number | null
  symbol: string
  side: string
  entry_time: string
  entry_price: number
  exit_time: string
  exit_price: number
  size: number
  initial_stop_price: number
  pnl: number
  gross_pnl: number | null
  net_pnl: number | null
  initial_risk: number | null
  r_multiple: number | null
  risk_reward_ratio: number | null
  r_validation_status: string
  trade_hash: string
  stop_phase: string
  signal_candle_ts: string
  pnl_percent: number
  holding_duration_seconds: number
  holding_duration_readable: string
}

type SortKey = 'exit_time' | 'r_multiple' | 'pnl_percent'

const PAGE_SIZE = 50

function fmt(v: number | null | undefined, digits = 2): string {
  if (v == null) return '–'
  return v.toFixed(digits)
}

function SummaryCard({ label, value, suffix, color }: {
  label: string; value: string; suffix?: string; color?: 'positive' | 'negative' | 'neutral'
}) {
  const cls =
    color === 'positive' ? 'text-[var(--positive)]'
    : color === 'negative' ? 'text-[var(--negative)]'
    : ''
  return (
    <div className="card-elevated p-4">
      <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">{label}</p>
      <p className={`mt-1 text-xl font-bold tabular-nums ${cls}`}>
        {value}{suffix && <span className="ml-0.5 text-sm font-medium text-[var(--text-muted)]">{suffix}</span>}
      </p>
    </div>
  )
}

export function TradesTab() {
  const { accessToken } = useAuth()
  const [summary, setSummary] = useState<TradeSummary | null>(null)
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(0)
  const [hasMore, setHasMore] = useState(true)
  const [sortKey, setSortKey] = useState<SortKey>('exit_time')
  const [sortAsc, setSortAsc] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const initialLoaded = useRef(false)

  const fetchSummary = useCallback(async () => {
    try {
      const res = await apiFetch('/api/trades/summary', { token: accessToken })
      if (res.ok) setSummary(await res.json())
    } catch { /* */ }
  }, [accessToken])

  const fetchTrades = useCallback(async (p: number) => {
    try {
      const res = await apiFetch(`/api/trades?limit=${PAGE_SIZE}&offset=${p * PAGE_SIZE}`, { token: accessToken })
      if (res.ok) { const data: Trade[] = await res.json(); setTrades(data); setHasMore(data.length === PAGE_SIZE) }
    } catch { /* */ }
  }, [accessToken])

  useEffect(() => {
    setLoading(true)
    Promise.all([fetchSummary(), fetchTrades(0)]).finally(() => { setLoading(false); initialLoaded.current = true })
  }, [fetchSummary, fetchTrades])

  useEffect(() => {
    if (initialLoaded.current) fetchTrades(page)
  }, [page, fetchTrades])

  const sorted = [...trades].sort((a, b) => {
    let av: number, bv: number
    if (sortKey === 'exit_time') { av = new Date(a.exit_time || 0).getTime(); bv = new Date(b.exit_time || 0).getTime() }
    else { av = (a as unknown as Record<string, number>)[sortKey] ?? 0; bv = (b as unknown as Record<string, number>)[sortKey] ?? 0 }
    return sortAsc ? av - bv : bv - av
  })

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc)
    else { setSortKey(key); setSortAsc(false) }
  }

  const SortIcon = ({ active, asc }: { active: boolean; asc: boolean }) => (
    <svg className={`ml-1 inline h-3 w-3 ${active ? 'text-[var(--accent)]' : 'text-[var(--text-muted)]/40'}`}
      fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d={active && asc ? 'M5 15l7-7 7 7' : 'M19 9l-7 7-7-7'} />
    </svg>
  )

  if (loading) {
    return (
      <div className="card p-6">
        <p className="section-title mb-4">Trade History</p>
        <div className="flex items-center gap-3 text-[var(--text-muted)]">
          <div className="h-5 w-5 animate-spin-slow rounded-full border-2 border-[var(--border)] border-t-[var(--accent)]" />
          <span className="text-sm">Loading...</span>
        </div>
      </div>
    )
  }

  const s = summary
  const wrColor = (s?.win_rate ?? 0) >= 50 ? 'positive' : 'neutral'
  const avgRColor = (s?.average_r ?? 0) > 0 ? 'positive' : (s?.average_r ?? 0) < 0 ? 'negative' : 'neutral'
  const expColor = (s?.expectancy_r ?? 0) > 0 ? 'positive' : (s?.expectancy_r ?? 0) < 0 ? 'negative' : 'neutral'
  const pnlColor = (s?.total_net_pnl ?? 0) > 0 ? 'positive' : (s?.total_net_pnl ?? 0) < 0 ? 'negative' : 'neutral'

  return (
    <div className="space-y-4">

      {/* Summary Cards */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <SummaryCard label="Total Trades" value={String(s?.total_trades ?? 0)} />
        <SummaryCard label="Win Rate" value={fmt(s?.win_rate)} suffix="%" color={wrColor as 'positive' | 'negative' | 'neutral'} />
        <SummaryCard label="Average R" value={fmt(s?.average_r, 3)} suffix="R" color={avgRColor as 'positive' | 'negative' | 'neutral'} />
        <SummaryCard label="Expectancy" value={fmt(s?.expectancy_r, 3)} suffix="R" color={expColor as 'positive' | 'negative' | 'neutral'} />
        <SummaryCard label="Net PnL" value={fmt(s?.total_net_pnl, 2)} suffix="USDT" color={pnlColor as 'positive' | 'negative' | 'neutral'} />
        <SummaryCard label="Max DD" value={fmt(s?.max_drawdown_r, 2)} suffix="R" color="negative" />
      </div>

      {/* Trades Table */}
      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-left text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                <th className="px-4 py-3 font-semibold">Side</th>
                <th className="px-4 py-3 font-semibold">Entry</th>
                <th className="px-4 py-3 font-semibold">Exit</th>
                <th className="cursor-pointer select-none px-4 py-3 font-semibold" onClick={() => handleSort('pnl_percent')}>
                  PnL %<SortIcon active={sortKey === 'pnl_percent'} asc={sortAsc} />
                </th>
                <th className="cursor-pointer select-none px-4 py-3 font-semibold" onClick={() => handleSort('r_multiple')}>
                  R<SortIcon active={sortKey === 'r_multiple'} asc={sortAsc} />
                </th>
                <th className="px-4 py-3 font-semibold">Size</th>
                <th className="cursor-pointer select-none px-4 py-3 font-semibold" onClick={() => handleSort('exit_time')}>
                  Duration<SortIcon active={sortKey === 'exit_time'} asc={sortAsc} />
                </th>
                <th className="px-4 py-3 font-semibold">Phase</th>
                <th className="px-4 py-3 font-semibold">Valid</th>
              </tr>
            </thead>
            <tbody>
              {sorted.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-4 py-12 text-center text-[var(--text-muted)]">
                    아직 종료된 거래가 없습니다.
                  </td>
                </tr>
              ) : sorted.map((t, idx) => {
                const isLong = t.side?.toUpperCase() === 'LONG'
                const rVal = t.r_multiple ?? 0
                const pnlPct = t.pnl_percent ?? 0
                const rColor = rVal > 0 ? 'text-[var(--positive)]' : rVal < 0 ? 'text-[var(--negative)]' : ''
                const pnlColor2 = pnlPct > 0 ? 'text-[var(--positive)]' : pnlPct < 0 ? 'text-[var(--negative)]' : ''
                const rowKey = t.id ?? idx
                const isExpanded = expandedId === (t.id ?? idx)
                const validOk = t.r_validation_status === 'OK'
                return (
                  <tr key={rowKey} className="group">
                    <td colSpan={9} className="p-0">
                      <div
                        className={`cursor-pointer border-l-[3px] transition-colors hover:bg-[var(--bg-elevated)]/50 ${
                          isLong ? 'border-l-[var(--positive)]/50' : 'border-l-[var(--negative)]/50'
                        } ${isExpanded ? 'bg-[var(--bg-elevated)]/30' : ''}`}
                        onClick={() => setExpandedId(isExpanded ? null : (t.id ?? idx))}
                      >
                        <div className="grid grid-cols-9 items-center">
                          <div className="px-4 py-3">
                            <span className={`badge text-[10px] font-bold ${
                              isLong ? 'bg-[var(--positive-muted)] text-[var(--positive)]' : 'bg-[var(--negative-muted)] text-[var(--negative)]'
                            }`}>{t.side}</span>
                          </div>
                          <div className="px-4 py-3 tabular-nums">{fmt(t.entry_price)}</div>
                          <div className="px-4 py-3 tabular-nums">{fmt(t.exit_price)}</div>
                          <div className={`px-4 py-3 tabular-nums font-medium ${pnlColor2}`}>
                            {pnlPct > 0 ? '+' : ''}{fmt(pnlPct)}%
                          </div>
                          <div className={`px-4 py-3 tabular-nums font-semibold ${rColor}`}>
                            {rVal > 0 ? '+' : ''}{fmt(rVal, 3)}R
                          </div>
                          <div className="px-4 py-3 tabular-nums">{t.size}</div>
                          <div className="px-4 py-3 text-[var(--text-muted)]">{t.holding_duration_readable}</div>
                          <div className="px-4 py-3">
                            <span className="capitalize text-[var(--text-muted)]">{t.stop_phase || '–'}</span>
                          </div>
                          <div className="px-4 py-3">
                            {validOk ? (
                              <span className="text-[var(--positive)]/70 text-[10px] font-medium">OK</span>
                            ) : (
                              <span className="badge bg-[var(--warning-muted)] text-[var(--warning)]">
                                {t.r_validation_status || '–'}
                              </span>
                            )}
                          </div>
                        </div>

                        {isExpanded && (
                          <div className="border-t border-[var(--border-subtle)] bg-[var(--bg-elevated)]/30 px-6 py-4 animate-fade-in">
                            <div className="grid gap-x-8 gap-y-2 sm:grid-cols-2 lg:grid-cols-4 text-xs">
                              <Detail label="Entry Time" value={t.entry_time || '–'} />
                              <Detail label="Exit Time" value={t.exit_time || '–'} />
                              <Detail label="Initial Stop" value={fmt(t.initial_stop_price)} />
                              <Detail label="Initial Risk" value={fmt(t.initial_risk, 4)} />
                              <Detail label="Gross PnL" value={`${fmt(t.gross_pnl, 4)} USDT`}
                                color={(t.gross_pnl ?? 0) >= 0 ? 'positive' : 'negative'} />
                              <Detail label="Net PnL" value={`${fmt(t.net_pnl, 4)} USDT`}
                                color={(t.net_pnl ?? 0) >= 0 ? 'positive' : 'negative'} />
                              <Detail label="Signal Candle" value={t.signal_candle_ts || '–'} />
                              <Detail label="R Validation" value={t.r_validation_status || '–'} />
                              <div className="sm:col-span-2 lg:col-span-4">
                                <span className="text-[var(--text-muted)]">Trade Hash </span>
                                <span className="font-mono text-[10px] text-[var(--text-muted)]/70 break-all">{t.trade_hash || '–'}</span>
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {(page > 0 || hasMore) && (
          <div className="flex items-center justify-between border-t border-[var(--border-subtle)] px-4 py-3 text-xs text-[var(--text-muted)]">
            <span>Page {page + 1}</span>
            <div className="flex gap-2">
              <button
                disabled={page === 0}
                onClick={() => setPage(p => Math.max(0, p - 1))}
                className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-3 py-1.5 transition-colors enabled:hover:bg-[var(--border)]/20 disabled:opacity-30"
              >
                Prev
              </button>
              <button
                disabled={!hasMore}
                onClick={() => setPage(p => p + 1)}
                className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-3 py-1.5 transition-colors enabled:hover:bg-[var(--border)]/20 disabled:opacity-30"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Detail({ label, value, color }: { label: string; value: string; color?: 'positive' | 'negative' }) {
  const cls = color === 'positive' ? 'text-[var(--positive)]' : color === 'negative' ? 'text-[var(--negative)]' : ''
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[var(--text-muted)]">{label}</span>
      <span className={`font-medium tabular-nums ${cls}`}>{value}</span>
    </div>
  )
}
