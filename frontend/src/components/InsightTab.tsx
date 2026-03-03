import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { useAuth } from '../context/AuthContext'

interface TrendRef {
  volatility_score?: number | null
  bb_width_percentile?: number | null
  atr_value?: number | null
  volume_ratio?: number | null
  bb_width_z?: number | null
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
}

interface InsightData {
  regime: string
  active_strategy: string
  regime_changed: boolean
  regime_score: number
  position_scale: number
  cooldown_remaining: number
  volatility_score: number
  bb_width_percentile: number
  atr_value: number
  volume_ratio: number
  rsi?: number | null
  range_high?: number | null
  range_low?: number | null
  range_mid?: number | null
  volume_zscore?: number | null
  close_price?: number | null
  bb_width_z?: number | null
  engine_reasoning: string[]
  trend_reference?: TrendRef
  range_reference?: RangeRef
  regime_classifier?: RegimeClassifier
}

function fmt(v: number | null | undefined, digits = 2, suffix = ''): string {
  if (v == null) return '–'
  return v.toFixed(digits) + suffix
}

function MetricCard({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-elevated)] p-4 transition-colors">
      <p className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">{label}</p>
      <p className={`mt-1 text-lg font-semibold tabular-nums ${accent ? 'text-[var(--accent)]' : ''}`}>{value}</p>
    </div>
  )
}

function RefRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between">
      <span className="text-[11px] text-[var(--text-muted)]">{label}</span>
      <span className="text-xs font-medium tabular-nums">{value}</span>
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
    const interval = setInterval(fetch_, 30000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [accessToken])

  if (loading) {
    return (
      <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] p-6 shadow-[var(--shadow)] ring-1 ring-white/5 backdrop-blur-sm">
        <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">Market Insight</h3>
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

  const regimeColors: Record<string, string> = {
    TRENDING: 'bg-emerald-500/15 text-emerald-400 ring-emerald-500/30',
    RANGING: 'bg-amber-500/15 text-amber-400 ring-amber-500/30',
  }
  const regimeClass = regimeColors[regime.toUpperCase()] ?? 'bg-[var(--border)]/30 text-[var(--text-muted)] ring-[var(--border)]'

  const tr = data?.trend_reference
  const rr = data?.range_reference
  const rc = data?.regime_classifier

  return (
    <div className="space-y-4">

      {/* ── Header ───────────────────────────── */}
      <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] p-6 shadow-[var(--shadow)] ring-1 ring-white/5 backdrop-blur-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">Market Insight</h3>
          <div className="flex items-center gap-3">
            <span className={`rounded-lg px-3 py-1 text-xs font-bold uppercase tracking-wider ring-1 ${regimeClass}`}>
              {regime}
            </span>
            {data?.regime_score != null && (
              <span className="rounded-md bg-[var(--bg-elevated)] px-2 py-1 text-[11px] font-medium text-[var(--text-muted)] ring-1 ring-[var(--border)]">
                Score {data.regime_score}/3
              </span>
            )}
            {data?.position_scale != null && data.position_scale !== 1.0 && (
              <span className="rounded-md bg-[var(--bg-elevated)] px-2 py-1 text-[11px] font-medium text-[var(--text-muted)] ring-1 ring-[var(--border)]">
                Size {data.position_scale}x
              </span>
            )}
            {(data?.cooldown_remaining ?? 0) > 0 && (
              <span className="rounded-md bg-orange-500/10 px-2 py-1 text-[11px] font-medium text-orange-400 ring-1 ring-orange-500/30">
                Cooldown {data!.cooldown_remaining}
              </span>
            )}
          </div>
        </div>

        {/* Regime classifier detail */}
        {rc && (
          <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-[var(--text-muted)]">
            <span>ADX <span className="font-medium text-[var(--accent)]">{fmt(rc.adx, 1)}</span></span>
            <span>BB%ile <span className="font-medium text-[var(--accent)]">{fmt(rc.bb_width_percentile, 1)}%</span></span>
            <span>HH <span className="font-medium text-[var(--accent)]">{fmt(rc.hh_ratio, 2)}</span></span>
            <span>LL <span className="font-medium text-[var(--accent)]">{fmt(rc.ll_ratio, 2)}</span></span>
          </div>
        )}
      </div>

      {/* ── Main metrics (regime-specific) ──── */}
      <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] p-6 shadow-[var(--shadow)] ring-1 ring-white/5 backdrop-blur-sm">
        <p className="mb-4 text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {isRanging ? 'Range Analysis' : isTrending ? 'Trend Analysis' : 'Analysis'}
        </p>

        {isRanging ? (
          /* ── RANGING main: range bounds, RSI, vol z ── */
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <MetricCard label="Range High" value={fmt(data?.range_high ?? rr?.range_high, 2)} />
              <MetricCard label="Range Low" value={fmt(data?.range_low ?? rr?.range_low, 2)} />
              <MetricCard label="Range Mid" value={fmt(data?.range_mid ?? rr?.range_mid, 2)} />
            </div>
            <div className="grid gap-4 sm:grid-cols-3">
              <MetricCard label="RSI (14)" value={fmt(data?.rsi ?? rr?.rsi, 2)} accent />
              <MetricCard label="Volume Z-score" value={fmt(data?.volume_zscore ?? rr?.volume_zscore, 2)} />
              <MetricCard label="Close" value={fmt(data?.close_price ?? rr?.close_price, 2)} />
            </div>
          </div>
        ) : (
          /* ── TRENDING main: vol score, BB%, ATR, vol ratio ── */
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard label="Volatility Score" value={fmt(data?.volatility_score ?? tr?.volatility_score, 4)} />
            <MetricCard label="BB Width %ile" value={fmt(data?.bb_width_percentile ?? tr?.bb_width_percentile, 2, '%')} />
            <MetricCard label="ATR (14)" value={fmt(data?.atr_value ?? tr?.atr_value, 2)} accent />
            <MetricCard label="Volume Ratio" value={fmt(data?.volume_ratio ?? tr?.volume_ratio, 2, 'x')} />
          </div>
        )}
      </div>

      {/* ── Reference block (other sight) ──── */}
      <div className="rounded-2xl border border-[var(--border)]/50 bg-[var(--bg-card)]/60 p-5 shadow-[var(--shadow)] ring-1 ring-white/5 backdrop-blur-sm">
        <p className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {isRanging ? 'Trend Reference' : 'Range Reference'}
        </p>

        {isRanging ? (
          /* show trend reference when RANGING */
          <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-4">
            <RefRow label="Volatility" value={fmt(tr?.volatility_score, 4)} />
            <RefRow label="BB %ile" value={fmt(tr?.bb_width_percentile, 2) + '%'} />
            <RefRow label="ATR" value={fmt(tr?.atr_value, 2)} />
            <RefRow label="Vol Ratio" value={fmt(tr?.volume_ratio, 2) + 'x'} />
          </div>
        ) : (
          /* show range reference when TRENDING */
          <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
            <RefRow label="Range" value={`${fmt(rr?.range_low, 0)} – ${fmt(rr?.range_high, 0)}`} />
            <RefRow label="RSI" value={fmt(rr?.rsi, 2)} />
            <RefRow label="Vol Z" value={fmt(rr?.volume_zscore, 2)} />
          </div>
        )}
      </div>

      {/* ── Engine Reasoning ──────────────── */}
      {data?.engine_reasoning && data.engine_reasoning.length > 0 && (
        <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] p-6 shadow-[var(--shadow)] ring-1 ring-white/5 backdrop-blur-sm">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">Engine Reasoning</p>
          <ul className="space-y-2">
            {data.engine_reasoning.map((r, i) => (
              <li key={i} className="flex items-start gap-2.5 text-sm leading-relaxed">
                <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--accent)]" />
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
