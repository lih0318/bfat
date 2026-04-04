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
  tpProtectionMode?: 'exchange' | 'fallback' | 'none'
  rMultiple: number | null
}

function fmt(v: number | null | undefined, digits = 2): string {
  if (v == null) return '–'
  return v.toFixed(digits)
}

export function PositionCard({ position, currentStopPrice, takeProfit, tpProtectionMode = 'none', rMultiple }: PositionCardProps) {
  if (!position) {
    return (
      <div className="card p-5">
        <p className="section-title mb-4">Position</p>
        <div className="flex flex-col items-center justify-center py-10 text-center">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-[var(--bg-elevated)]">
            <svg className="h-6 w-6 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M20 12H4" />
            </svg>
          </div>
          <p className="text-sm text-[var(--text-muted)]">포지션 없음</p>
          <p className="mt-1 text-xs text-[var(--text-muted)]/70">엔진을 시작하면 여기에 표시됩니다</p>
        </div>
      </div>
    )
  }

  const isLong = position.side.toUpperCase() === 'LONG'
  const sideColor = isLong ? 'text-[var(--positive)]' : 'text-[var(--negative)]'
  const sideBg = isLong ? 'bg-[var(--positive-muted)]' : 'bg-[var(--negative-muted)]'

  const sl = currentStopPrice ?? position.stop_price
  const tp = takeProfit
  const isTrailingTP = tp == null
  const isBinanceLive = position.source === 'binance'

  return (
    <div className="card p-5">

      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <p className="section-title">Position</p>
          {isBinanceLive && (
            <span className="badge bg-[var(--warning-muted)] text-[var(--warning)]">BINANCE LIVE</span>
          )}
        </div>
        <span className={`badge ${sideBg} ${sideColor} font-bold`}>
          {position.side.toUpperCase()}
        </span>
      </div>

      {/* Core Metrics */}
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <div className="card-elevated p-3">
          <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">Symbol</p>
          <p className="mt-0.5 text-base font-semibold">{position.symbol}</p>
        </div>
        <div className="card-elevated p-3">
          <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">Size</p>
          <p className="mt-0.5 text-base font-semibold tabular-nums">{position.size}</p>
        </div>
        <div className="card-elevated p-3">
          <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">Entry Price</p>
          <p className="mt-0.5 text-base font-semibold tabular-nums">{fmt(position.entry_price)}</p>
        </div>
      </div>

      {/* SL / TP */}
      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        {/* Stop Loss */}
        <div className="rounded-xl border border-[var(--negative)]/15 bg-[var(--negative-muted)] p-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--negative)]/20 text-[10px] font-bold text-[var(--negative)]">SL</span>
              <p className="text-[10px] uppercase tracking-wider text-[var(--negative)]/70">Stop Loss</p>
            </div>
            {position.stop_phase && position.stop_phase !== 'unknown' && (
              <span className="rounded-md bg-[var(--bg-elevated)] px-1.5 py-0.5 text-[10px] font-medium capitalize text-[var(--text-muted)]">
                {position.stop_phase}
              </span>
            )}
          </div>
          <p className="mt-1.5 text-lg font-bold tabular-nums text-[var(--negative)]">
            {sl > 0 ? fmt(sl) : isBinanceLive ? (
              <span className="text-sm font-medium text-[var(--negative)]/60">SL 정보 없음</span>
            ) : '–'}
          </p>
        </div>

        {/* Take Profit */}
        <div className="rounded-xl border border-[var(--positive)]/15 bg-[var(--positive-muted)] p-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--positive)]/20 text-[10px] font-bold text-[var(--positive)]">TP</span>
              <p className="text-[10px] uppercase tracking-wider text-[var(--positive)]/70">Take Profit</p>
            </div>
            {tpProtectionMode === 'exchange' && (
              <span className="rounded-md bg-[var(--positive)]/15 px-1.5 py-0.5 text-[10px] font-medium text-[var(--positive)]">
                Exchange ✓
              </span>
            )}
            {tpProtectionMode === 'fallback' && (
              <span className="rounded-md bg-[var(--warning-muted)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--warning)]">
                Fallback
              </span>
            )}
            {tpProtectionMode === 'none' && tp == null && (
              <span className="rounded-md bg-[var(--bg-elevated)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--text-muted)]">
                미설정
              </span>
            )}
          </div>
          {isTrailingTP ? (
            <p className="mt-1.5 text-sm font-medium text-[var(--positive)]/70">트레일링 스탑으로 관리</p>
          ) : (
            <p className="mt-1.5 text-lg font-bold tabular-nums text-[var(--positive)]">{fmt(tp)}</p>
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-[var(--border-subtle)] pt-3 text-xs text-[var(--text-muted)]">
        {rMultiple != null && (
          <span>R-Multiple <span className={`font-semibold ${rMultiple >= 0 ? 'text-[var(--positive)]' : 'text-[var(--negative)]'}`}>{rMultiple.toFixed(2)}R</span></span>
        )}
        {position.unrealized_pnl != null && (
          <span>Unrealized PnL <span className={`font-semibold ${position.unrealized_pnl >= 0 ? 'text-[var(--positive)]' : 'text-[var(--negative)]'}`}>{position.unrealized_pnl >= 0 ? '+' : ''}{position.unrealized_pnl.toFixed(4)} USDT</span></span>
        )}
        {position.entry_time && <span>Entry {position.entry_time}</span>}
      </div>
    </div>
  )
}
