export interface PositionData {
  symbol: string
  side: string
  size: number
  entry_price: number
  stop_price: number
  stop_phase: string
  entry_time: string
  correlation_id: string
  source?: string
  unrealized_pnl?: number
}

interface PositionCardProps {
  position: PositionData | null
  currentStopPrice: number | null
  takeProfit: number | null
  rMultiple: number | null
}

function fmt(v: number | null | undefined, digits = 2): string {
  if (v == null) return '–'
  return v.toFixed(digits)
}

export function PositionCard({ position, currentStopPrice, takeProfit, rMultiple }: PositionCardProps) {
  if (!position) {
    return (
      <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5 shadow-[var(--shadow)] ring-1 ring-white/5 backdrop-blur-sm">
        <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
          Position
        </h3>
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <svg className="mb-3 h-12 w-12 text-[var(--text-muted)]/40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 7h8m0 0v8m0-8v8M3 17h.01M7 14h.01M11 17h.01M15 14h.01M3 7h.01M7 4h.01M11 7h.01M15 4h.01" />
          </svg>
          <p className="text-sm text-[var(--text-muted)]">포지션 없음</p>
          <p className="mt-1 text-xs text-[var(--text-muted)]/80">엔진을 시작하면 여기에 표시됩니다</p>
        </div>
      </div>
    )
  }

  const isLong = position.side.toUpperCase() === 'LONG'
  const sideColor = isLong ? 'text-emerald-400' : 'text-rose-400'
  const sideBg = isLong ? 'bg-emerald-500/15 ring-emerald-500/30' : 'bg-rose-500/15 ring-rose-500/30'

  const sl = currentStopPrice ?? position.stop_price
  const tp = takeProfit
  const isTrailingTP = tp == null
  const stopPhase = position.stop_phase

  const isBinanceLive = position.source === 'binance'

  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5 shadow-[var(--shadow)] ring-1 ring-white/5 backdrop-blur-sm">

      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
            Position
          </h3>
          {isBinanceLive && (
            <span className="rounded-md bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-400 ring-1 ring-amber-500/30">
              BINANCE LIVE
            </span>
          )}
        </div>
        <span className={`rounded-lg px-3 py-1 text-xs font-bold uppercase tracking-wider ring-1 ${sideBg} ${sideColor}`}>
          {position.side.toUpperCase()}
        </span>
      </div>

      {/* Core info */}
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-elevated)] p-3">
          <p className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">Symbol</p>
          <p className="mt-0.5 text-base font-semibold">{position.symbol}</p>
        </div>
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-elevated)] p-3">
          <p className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">Size</p>
          <p className="mt-0.5 text-base font-semibold tabular-nums">{position.size}</p>
        </div>
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-elevated)] p-3">
          <p className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">Entry Price</p>
          <p className="mt-0.5 text-base font-semibold tabular-nums">{fmt(position.entry_price)}</p>
        </div>
      </div>

      {/* SL / TP row */}
      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        {/* Stop Loss */}
        <div className="rounded-xl border border-rose-500/20 bg-rose-500/5 p-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-rose-500/20 text-[10px] font-bold text-rose-400">SL</span>
              <p className="text-[11px] uppercase tracking-wider text-rose-300/70">Stop Loss</p>
            </div>
            {stopPhase && stopPhase !== 'unknown' && (
              <span className="rounded-md bg-[var(--bg-elevated)] px-1.5 py-0.5 text-[10px] font-medium capitalize text-[var(--text-muted)]">
                {stopPhase}
              </span>
            )}
          </div>
          <p className="mt-1.5 text-lg font-bold tabular-nums text-rose-400">
            {sl > 0 ? fmt(sl) : isBinanceLive ? (
              <span className="text-sm font-medium text-rose-300/60">SL 정보 없음</span>
            ) : '–'}
          </p>
        </div>

        {/* Take Profit */}
        <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-3">
          <div className="flex items-center gap-2">
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/20 text-[10px] font-bold text-emerald-400">TP</span>
            <p className="text-[11px] uppercase tracking-wider text-emerald-300/70">Take Profit</p>
          </div>
          {isTrailingTP ? (
            <div className="mt-1.5 flex items-center gap-2">
              <p className="text-sm font-medium text-emerald-400/70">트레일링 스탑으로 관리</p>
              <svg className="h-4 w-4 text-emerald-400/50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
              </svg>
            </div>
          ) : (
            <p className="mt-1.5 text-lg font-bold tabular-nums text-emerald-400">
              {fmt(tp)}
            </p>
          )}
        </div>
      </div>

      {/* Footer: R-Multiple / Entry Time */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-[var(--border)] pt-3 text-xs text-[var(--text-muted)]">
        {rMultiple != null && (
          <span>
            R-Multiple{' '}
            <span className={`font-semibold ${rMultiple >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              {rMultiple.toFixed(2)}R
            </span>
          </span>
        )}
        {position.unrealized_pnl != null && (
          <span>
            Unrealized PnL{' '}
            <span className={`font-semibold ${position.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              {position.unrealized_pnl >= 0 ? '+' : ''}{position.unrealized_pnl.toFixed(4)} USDT
            </span>
          </span>
        )}
        {position.entry_time && (
          <span>Entry {position.entry_time}</span>
        )}
      </div>
    </div>
  )
}
