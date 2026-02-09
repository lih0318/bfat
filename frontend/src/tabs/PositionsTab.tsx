import { useEffect, useState } from 'react'
import { api, type JournalEntry } from '../api/client'
import './PositionsTab.css'

interface PositionRow {
  symbol: string
  side: string
  entryPrice: string
  markPrice: string
  positionAmt: string
  leverage: string
  unrealizedProfit: string
  slPrice?: string
  tpPrice?: string
}

/** Build map: symbol -> { sl, tp, side } from journal (most recent entry per symbol). */
function buildSlTpFromJournal(entries: JournalEntry[]): Record<string, { sl?: number; tp?: number; side?: string }> {
  const bySymbol: Record<string, { sl?: number; tp?: number; side?: string }> = {}
  for (const e of entries) {
    if (e.type !== 'entry' || !e.symbol) continue
    if (e.sl == null && e.tp == null) continue
    // Always overwrite — entries are newest-first, so the last one written wins as the most recent
    if (!bySymbol[e.symbol]) {
      bySymbol[e.symbol] = { sl: e.sl, tp: e.tp, side: e.side }
    }
  }
  return bySymbol
}

export function PositionsTab() {
  const [positions, setPositions] = useState<PositionRow[]>([])
  const [journalSlTpBySymbol, setJournalSlTpBySymbol] = useState<Record<string, { sl?: number; tp?: number; side?: string }>>({})
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        setError(null)
        const [pos, journal] = await Promise.all([
          api.positions.list(),
          api.journal.list(200, 'live'),
        ])
        const rows: PositionRow[] = []
        for (const p of pos) {
          const amt = Number((p as Record<string, unknown>).positionAmt ?? (p as Record<string, unknown>).position_amt ?? 0)
          if (amt === 0) continue
          const symbol = String((p as Record<string, unknown>).symbol ?? '')
          const leverageRaw = (p as Record<string, unknown>).leverage
          const leverage = leverageRaw != null && leverageRaw !== '' ? String(leverageRaw) : ''
          rows.push({
            symbol,
            side: amt > 0 ? 'Long' : 'Short',
            entryPrice: String((p as Record<string, unknown>).entryPrice ?? (p as Record<string, unknown>).entry_price ?? ''),
            markPrice: String((p as Record<string, unknown>).markPrice ?? (p as Record<string, unknown>).mark_price ?? ''),
            positionAmt: String((p as Record<string, unknown>).positionAmt ?? (p as Record<string, unknown>).position_amt ?? ''),
            leverage,
            unrealizedProfit: String((p as Record<string, unknown>).unRealizedProfit ?? (p as Record<string, unknown>).un_realized_profit ?? ''),
          })
        }
        if (!cancelled) {
          setPositions(rows)
          setJournalSlTpBySymbol(buildSlTpFromJournal(Array.isArray(journal) ? journal : []))
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }
    load()
    const t = setInterval(load, 2000)
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [])

  const getSlTp = (symbol: string, positionSide: string): { sl?: string; tp?: string } => {
    const j = journalSlTpBySymbol[symbol]
    if (!j) return {}
    let sl = j.sl
    let tp = j.tp
    // Sanity check: for Long, SL should be below TP; for Short, SL should be above TP.
    // If journal values seem swapped relative to position side, correct the display.
    if (sl != null && tp != null) {
      if (positionSide === 'Long' && sl > tp) {
        // Swapped — sl is actually tp and vice versa
        ;[sl, tp] = [tp, sl]
      } else if (positionSide === 'Short' && sl < tp) {
        ;[sl, tp] = [tp, sl]
      }
    }
    return {
      sl: sl != null ? String(sl) : undefined,
      tp: tp != null ? String(tp) : undefined,
    }
  }

  if (error) {
    return (
      <div className="positions-tab">
        <p className="positions-error">{error}</p>
      </div>
    )
  }

  return (
    <div className="positions-tab">
      <section className="positions-section positions-section--live">
        <h2 className="positions-heading">Live — Open Positions</h2>
        <p className="positions-sub">Leverage = value applied by Rich Man. PnL refreshes every 2s.</p>
        {positions.length === 0 ? (
          <p className="positions-empty">No open positions.</p>
        ) : (
          <div className="positions-table-wrap">
            <table className="positions-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Entry</th>
                  <th>Mark</th>
                  <th>Amount</th>
                  <th>Notional (USDT)</th>
                  <th>Leverage (x)</th>
                  <th>Unrealized PnL</th>
                  <th>SL</th>
                  <th>TP</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((row) => {
                  const { sl, tp } = getSlTp(row.symbol, row.side)
                  const pnl = parseFloat(row.unrealizedProfit)
                  const amt = Math.abs(parseFloat(row.positionAmt))
                  const notional = Number(row.entryPrice) * amt
                  const pnlPct = notional !== 0 ? (pnl / notional) * 100 : 0
                  return (
                    <tr key={row.symbol}>
                      <td>{row.symbol}</td>
                      <td className={row.side === 'Long' ? 'side-long' : 'side-short'}>{row.side}</td>
                      <td>{Number(row.entryPrice).toFixed(2)}</td>
                      <td>{Number(row.markPrice).toFixed(2)}</td>
                      <td>{row.positionAmt}</td>
                      <td>{notional.toFixed(2)}</td>
                      <td>{row.leverage ? `${row.leverage}x` : '—'}</td>
                      <td className={pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}>
                        {pnl >= 0 ? '+' : ''}{Number(row.unrealizedProfit).toFixed(2)}
                        <span className="pnl-pct"> ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%)</span>
                      </td>
                      <td className="sl-tp sl-val">{sl != null ? Number(sl).toFixed(2) : '-'}</td>
                      <td className="sl-tp tp-val">{tp != null ? Number(tp).toFixed(2) : '-'}</td>
                    </tr>
                  )
                })}
              </tbody>
              <tfoot>
                {(() => {
                  const totalPnl = positions.reduce((s, row) => s + parseFloat(row.unrealizedProfit), 0)
                  const totalNotional = positions.reduce(
                    (s, row) => s + Number(row.entryPrice) * Math.abs(parseFloat(row.positionAmt)),
                    0
                  )
                  const totalPct = totalNotional !== 0 ? (totalPnl / totalNotional) * 100 : 0
                  return (
                    <tr className="positions-total-row">
                      <td colSpan={5}>Total</td>
                      <td>{totalNotional.toFixed(2)}</td>
                      <td>—</td>
                      <td className={totalPnl >= 0 ? 'pnl-pos' : 'pnl-neg'}>
                        {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)}
                        <span className="pnl-pct"> ({totalPct >= 0 ? '+' : ''}{totalPct.toFixed(2)}%)</span>
                      </td>
                      <td colSpan={2} />
                    </tr>
                  )
                })()}
              </tfoot>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
