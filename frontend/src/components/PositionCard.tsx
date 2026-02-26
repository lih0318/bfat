export interface PositionData {
  symbol: string
  side: string
  size: number
  entry_price: number
  stop_price: number
  stop_phase: string
  entry_time: string
  correlation_id: string
}

interface PositionCardProps {
  position: PositionData | null
  currentStopPrice: number | null
  rMultiple: number | null
}

export function PositionCard({ position, currentStopPrice, rMultiple }: PositionCardProps) {
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

  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5 shadow-[var(--shadow)] ring-1 ring-white/5 backdrop-blur-sm">
      <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        Position
      </h3>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <div>
          <p className="text-xs text-[var(--text-muted)]">Symbol / Side</p>
          <p className="font-medium">{position.symbol} / {position.side}</p>
        </div>
        <div>
          <p className="text-xs text-[var(--text-muted)]">Size</p>
          <p className="font-medium">{position.size}</p>
        </div>
        <div>
          <p className="text-xs text-[var(--text-muted)]">Entry</p>
          <p className="font-medium">{position.entry_price}</p>
        </div>
        <div>
          <p className="text-xs text-[var(--text-muted)]">Stop Level</p>
          <p className="font-medium">{currentStopPrice ?? position.stop_price}</p>
        </div>
        <div>
          <p className="text-xs text-[var(--text-muted)]">Stop Phase</p>
          <p className="font-medium capitalize">{position.stop_phase}</p>
        </div>
        {rMultiple != null && (
          <div>
            <p className="text-xs text-[var(--text-muted)]">R-Multiple</p>
            <p className={`font-medium ${rMultiple >= 0 ? 'text-[var(--positive)]' : 'text-[var(--negative)]'}`}>
              {rMultiple.toFixed(2)}R
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
