import { useEffect, useState, useCallback } from 'react'
import { apiFetch } from '../api/client'
import { useAuth } from '../context/AuthContext'

interface TrendRef {
  volatility_score?: number | null
  atr_value?: number | null
  volume_ratio?: number | null
  ema_fast?: number | null
  ema_slow?: number | null
}

interface RangeRef {
  range_high?: number | null
  range_low?: number | null
  range_mid?: number | null
  rsi?: number | null
  volume_zscore?: number | null
  close_price?: number | null
}

interface RegimeClassifier {
  adx?: number | null
  bb_width_percentile?: number | null
  hh_ratio?: number | null
  ll_ratio?: number | null
  score?: number | null
  trend_direction?: 'up' | 'down' | 'neutral'
}

interface EntryCondition {
  label: string
  required: string
  actual: string
  met: boolean
}

interface InsightData {
  regime: string
  active_strategy: string
  regime_changed: boolean
  regime_score: number
  position_scale: number
  cooldown_remaining: number
  volatility_score: number
  atr_value: number
  volume_ratio: number
  ema_fast: number
  ema_slow: number
  rsi?: number | null
  range_high?: number | null
  range_low?: number | null
  range_mid?: number | null
  volume_zscore?: number | null
  close_price?: number | null
  engine_reasoning: string[]
  entry_conditions?: EntryCondition[]
  skip_reason?: string | null
  trend_reference?: TrendRef
  range_reference?: RangeRef
  regime_classifier?: RegimeClassifier
  last_insight_update_ts?: number
  entry_insight?: Record<string, unknown>
}

function fmt(v: number | null | undefined, digits = 2, suffix = ''): string {
  if (v == null) return '–'
  return v.toFixed(digits) + suffix
}

function useRelativeTime(epochSec: number | undefined): string {
  const [label, setLabel] = useState('')
  useEffect(() => {
    if (!epochSec) { setLabel(''); return }
    function update() {
      const delta = Math.max(0, Math.floor(Date.now() / 1000 - epochSec))
      if (delta < 5) setLabel('just now')
      else if (delta < 60) setLabel(`${delta}s ago`)
      else setLabel(`${Math.floor(delta / 60)}m ago`)
    }
    update()
    const id = setInterval(update, 5000)
    return () => clearInterval(id)
  }, [epochSec])
  return label
}

function MetricCard({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="card-elevated p-4">
      <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">{label}</p>
      <p className={`mt-1 text-lg font-semibold tabular-nums ${accent ? 'text-[var(--accent)]' : ''}`}>{value}</p>
    </div>
  )
}

function RefRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between">
      <span className="text-[10px] text-[var(--text-muted)]">{label}</span>
      <span className="text-xs font-medium tabular-nums">{value}</span>
    </div>
  )
}

function EntryInsightSnapshot({ snap }: { snap: Record<string, unknown> }) {
  const [open, setOpen] = useState(false)
  const toggle = useCallback(() => setOpen(p => !p), [])
  const ts = snap.snapshot_time as string | undefined
  const regime = (snap.regime as string) ?? 'Unknown'
  const reasoning = (snap.engine_reasoning as string[]) ?? []
  const conditions = (snap.entry_conditions as EntryCondition[]) ?? []

  return (
    <div className="card overflow-hidden border border-[var(--accent)]/20">
      <button
        onClick={toggle}
        className="flex w-full items-center justify-between px-5 py-3.5 text-left hover:bg-[var(--bg-elevated)]/50 transition-colors"
      >
        <div className="flex items-center gap-2.5">
          <span className="text-[var(--accent)] text-xs">📸</span>
          <span className="text-sm font-semibold text-[var(--text)]">Entry Insight Snapshot</span>
          {ts && <span className="text-[10px] text-[var(--text-muted)]">{ts}</span>}
          <span className="badge bg-[var(--accent-muted)] text-[var(--accent)] text-[10px]">{regime}</span>
        </div>
        <span className={`text-xs text-[var(--text-muted)] transition-transform ${open ? 'rotate-180' : ''}`}>▼</span>
      </button>
      {open && (
        <div className="border-t border-[var(--border-subtle)] px-5 py-4 space-y-3">
          {conditions.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-2">Entry Conditions</p>
              <div className="space-y-1">
                {conditions.map((c, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className={`inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] ${
                      c.met ? 'bg-[var(--positive-muted)] text-[var(--positive)]' : 'bg-[var(--negative-muted)] text-[var(--negative)]'
                    }`}>{c.met ? '✓' : '✗'}</span>
                    <span className="font-medium text-[var(--text-secondary)]">{c.label}</span>
                    <span className="text-[var(--text-muted)]">{c.required}</span>
                    <span className="text-[var(--text-muted)]">→</span>
                    <span className={c.met ? 'text-[var(--positive)]' : 'text-[var(--text-muted)]'}>{c.actual}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {reasoning.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-2">Reasoning</p>
              <ul className="space-y-1">
                {reasoning.map((r, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs text-[var(--text-secondary)]">
                    <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-[var(--accent)]" />
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function InsightTab() {
  const { accessToken } = useAuth()
  const [data, setData] = useState<InsightData | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    async function fetch_() {
      try {
        const res = await apiFetch('/api/insight', { token: accessToken })
        if (cancelled) return
        if (res.ok) setData(await res.json())
        else setData(null)
      } catch {
        if (!cancelled) setData(null)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    fetch_()
    const interval = setInterval(fetch_, 15000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [accessToken])

  const updatedAgo = useRelativeTime(data?.last_insight_update_ts)

  if (loading) {
    return (
      <div className="card p-6">
        <p className="section-title mb-4">Market Insight</p>
        <div className="flex items-center gap-3 text-[var(--text-muted)]">
          <div className="h-5 w-5 animate-spin-slow rounded-full border-2 border-[var(--border)] border-t-[var(--accent)]" />
          <span className="text-sm">Loading...</span>
        </div>
      </div>
    )
  }

  const regime = data?.regime ?? 'Unknown'
  const isRanging = regime.toUpperCase() === 'RANGING'
  const isTrending = regime.toUpperCase() === 'TRENDING'

  const regimeClass = regime.toUpperCase() === 'TRENDING'
    ? 'bg-[var(--positive-muted)] text-[var(--positive)]'
    : regime.toUpperCase() === 'RANGING'
      ? 'bg-[var(--accent-muted)] text-[var(--accent)]'
      : 'bg-[var(--border)]/30 text-[var(--text-muted)]'

  const tr = data?.trend_reference
  const rr = data?.range_reference
  const rc = data?.regime_classifier

  return (
    <div className="space-y-4">

      {/* Entry Insight Snapshot */}
      {data?.entry_insight && (
        <EntryInsightSnapshot snap={data.entry_insight} />
      )}

      {/* Header */}
      <div className="card p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <p className="section-title">Market Insight</p>
            {updatedAgo && (
              <span className="flex items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-[var(--positive)] animate-pulse" />
                {updatedAgo}
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className={`badge ${regimeClass}`}>
              {regime}
              {isTrending && rc?.trend_direction && rc.trend_direction !== 'neutral' && (
                <span className="ml-1.5 font-normal normal-case text-[var(--text-secondary)]">
                  {rc.trend_direction === 'up' ? '↑ Up' : '↓ Down'}
                </span>
              )}
            </span>
            {data?.regime_score != null && (
              <span className="badge bg-[var(--bg-elevated)] text-[var(--text-muted)]">Score {data.regime_score}/3</span>
            )}
            {data?.position_scale != null && data.position_scale !== 1.0 && (
              <span className="badge bg-[var(--bg-elevated)] text-[var(--text-muted)]">Size {data.position_scale}x</span>
            )}
            {(data?.cooldown_remaining ?? 0) > 0 && (
              <span className="badge bg-[var(--warning-muted)] text-[var(--warning)]">Cooldown {data!.cooldown_remaining}</span>
            )}
          </div>
        </div>

        {rc && (
          <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-[var(--text-muted)]">
            <span>ADX <span className="font-medium text-[var(--accent)]">{fmt(rc.adx, 1)}</span></span>
            <span>BB%ile <span className="font-medium text-[var(--accent)]">{fmt(rc.bb_width_percentile, 1)}%</span></span>
            <span>HH <span className="font-medium text-[var(--accent)]">{fmt(rc.hh_ratio, 2)}</span></span>
            <span>LL <span className="font-medium text-[var(--accent)]">{fmt(rc.ll_ratio, 2)}</span></span>
          </div>
        )}
      </div>

      {/* Main Metrics */}
      <div className="card p-6">
        <p className="section-title mb-4">
          {isRanging ? 'Range Analysis' : isTrending ? 'Trend Analysis' : 'Analysis'}
        </p>

        {isRanging ? (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <MetricCard label="Range High" value={fmt(data?.range_high ?? rr?.range_high, 2)} />
              <MetricCard label="Range Low" value={fmt(data?.range_low ?? rr?.range_low, 2)} />
              <MetricCard label="Range Mid" value={fmt(data?.range_mid ?? rr?.range_mid, 2)} />
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <MetricCard label="RSI (14)" value={fmt(data?.rsi ?? rr?.rsi, 2)} accent />
              <MetricCard label="Volume Z-score" value={fmt(data?.volume_zscore ?? rr?.volume_zscore, 2)} />
              <MetricCard label="Close" value={fmt(data?.close_price ?? rr?.close_price, 2)} />
            </div>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard label="EMA (12)" value={fmt(data?.ema_fast ?? tr?.ema_fast, 2)} />
            <MetricCard label="EMA (50)" value={fmt(data?.ema_slow ?? tr?.ema_slow, 2)} />
            <MetricCard label="ATR (14)" value={fmt(data?.atr_value ?? tr?.atr_value, 2)} accent />
            <MetricCard label="Volume Ratio" value={fmt(data?.volume_ratio ?? tr?.volume_ratio, 2, 'x')} />
          </div>
        )}
      </div>

      {/* Reference Block */}
      <div className="card p-5" style={{ opacity: 0.85 }}>
        <p className="section-title mb-3">
          {isRanging ? 'Trend Reference' : 'Range Reference'}
        </p>
        {isRanging ? (
          <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-4">
            <RefRow label="EMA (12)" value={fmt(tr?.ema_fast, 2)} />
            <RefRow label="EMA (50)" value={fmt(tr?.ema_slow, 2)} />
            <RefRow label="ATR" value={fmt(tr?.atr_value, 2)} />
            <RefRow label="Vol Ratio" value={fmt(tr?.volume_ratio, 2) + 'x'} />
          </div>
        ) : (
          <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
            <RefRow label="Range" value={`${fmt(rr?.range_low, 0)} – ${fmt(rr?.range_high, 0)}`} />
            <RefRow label="RSI" value={fmt(rr?.rsi, 2)} />
            <RefRow label="Vol Z" value={fmt(rr?.volume_zscore, 2)} />
          </div>
        )}
      </div>

      {/* Entry Conditions */}
      {data?.entry_conditions && data.entry_conditions.length > 0 && (
        <div className="card p-6">
          <p className="section-title mb-3">Entry Conditions</p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--border-subtle)] text-left text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                  <th className="pb-2.5 pr-3 font-semibold">Status</th>
                  <th className="pb-2.5 pr-3 font-semibold">Condition</th>
                  <th className="pb-2.5 pr-3 font-semibold">Required</th>
                  <th className="pb-2.5 font-semibold">Actual</th>
                </tr>
              </thead>
              <tbody>
                {data.entry_conditions.map((c, i) => (
                  <tr key={i} className="border-b border-[var(--border-subtle)]/50">
                    <td className="py-2.5 pr-3">
                      {c.met ? (
                        <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-[var(--positive-muted)] text-[var(--positive)] text-xs">✓</span>
                      ) : (
                        <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-[var(--negative-muted)] text-[var(--negative)] text-xs">✗</span>
                      )}
                    </td>
                    <td className={`py-2.5 pr-3 font-medium ${c.met ? 'text-[var(--text)]' : 'text-[var(--text-muted)]'}`}>{c.label}</td>
                    <td className="py-2.5 pr-3 tabular-nums text-[var(--text-muted)]">{c.required}</td>
                    <td className={`py-2.5 tabular-nums ${c.met ? 'text-[var(--positive)]' : 'text-[var(--text-muted)]'}`}>{c.actual}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Skip Reason */}
      {data?.skip_reason && (
        <div className={`flex items-center gap-2.5 rounded-xl border px-5 py-3.5 ${
          data.skip_reason === 'close_first_wait_next_cycle'
            ? 'border-[var(--accent)]/20 bg-[var(--accent-muted)]'
            : 'border-[var(--warning)]/20 bg-[var(--warning-muted)]'
        }`}>
          <span className={data.skip_reason === 'close_first_wait_next_cycle' ? 'text-[var(--accent)]' : 'text-[var(--warning)]'}>
            {data.skip_reason === 'close_first_wait_next_cycle' ? '⏭' : '⏸'}
          </span>
          <span className={`text-sm ${data.skip_reason === 'close_first_wait_next_cycle' ? 'text-[var(--accent)]' : 'text-[var(--warning)]'}`}>
            {data.skip_reason === 'close_first_wait_next_cycle'
              ? <>Regime changed — position closed first. <span className="font-normal text-[var(--text-secondary)]">Re-entry evaluated next candle.</span></>
              : <>Signal skipped: <span className="font-normal text-[var(--text-secondary)]">{data.skip_reason}</span></>
            }
          </span>
        </div>
      )}

      {/* Engine Reasoning */}
      {data?.engine_reasoning && data.engine_reasoning.length > 0 && (
        <div className="card p-6">
          <p className="section-title mb-3">Engine Reasoning</p>
          <ul className="space-y-2">
            {data.engine_reasoning.map((r, i) => (
              <li key={i} className="flex items-start gap-2.5 text-sm leading-relaxed text-[var(--text-secondary)]">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--accent)]" />
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
