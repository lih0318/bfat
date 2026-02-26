import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { useAuth } from '../context/AuthContext'

interface InsightData {
  regime: string
  volatility_score: number
  bb_width_percentile: number
  atr_value: number
  volume_ratio: number
  engine_reasoning: string[]
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
        if (res.ok) {
          const d = await res.json()
          setData(d)
        } else {
          setData(null)
        }
      } catch {
        if (!cancelled) setData(null)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    fetch_()
    const interval = setInterval(fetch_, 15000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [accessToken])

  if (loading) {
    return (
      <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] p-6 shadow-[var(--shadow)]">
        <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
          Market Insight
        </h3>
        <p className="text-[var(--text-muted)]">Loading...</p>
      </div>
    )
  }

  const regimeColors: Record<string, string> = {
    Trending: 'bg-[var(--positive)]/20 text-[var(--positive)]',
    Ranging: 'bg-[var(--accent)]/20 text-[var(--accent)]',
    'High Volatility': 'bg-[var(--negative)]/20 text-[var(--negative)]',
    Unknown: 'bg-[var(--border)]/30 text-[var(--text-muted)]',
  }
  const regimeClass = regimeColors[data?.regime ?? ''] ?? regimeColors.Unknown

  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] p-6 shadow-[var(--shadow)]">
      <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        Market Insight
      </h3>
      <div className="space-y-5">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs text-[var(--text-muted)]">Regime</span>
          <span className={`rounded-lg px-3 py-1.5 font-semibold ${regimeClass}`}>
            {data?.regime ?? 'Unknown'}
          </span>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
            <p className="text-xs text-[var(--text-muted)]">Volatility Score</p>
            <p className="text-lg font-medium">{data?.volatility_score?.toFixed(4) ?? '–'}</p>
          </div>
          <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
            <p className="text-xs text-[var(--text-muted)]">BB Width %ile</p>
            <p className="text-lg font-medium">{data?.bb_width_percentile?.toFixed(2) ?? '–'}%</p>
          </div>
          <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
            <p className="text-xs text-[var(--text-muted)]">ATR</p>
            <p className="text-lg font-medium">{data?.atr_value?.toFixed(4) ?? '–'}</p>
          </div>
          <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
            <p className="text-xs text-[var(--text-muted)]">Volume Ratio</p>
            <p className="text-lg font-medium">{data?.volume_ratio?.toFixed(2) ?? '–'}x</p>
          </div>
        </div>
        {data?.engine_reasoning && data.engine_reasoning.length > 0 && (
          <div>
            <p className="mb-2 text-xs font-medium text-[var(--text-muted)]">Engine Reasoning</p>
            <ul className="space-y-1.5">
              {data.engine_reasoning.map((r, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <span className="text-[var(--accent)]">•</span>
                  <span>{r}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}
