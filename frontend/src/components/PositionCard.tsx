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
      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5">
        <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
          Position
        </h3>
        <p className="text-[var(--text-muted)]">No open position</p>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5">
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
