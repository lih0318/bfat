import { useEffect, useState } from 'react'
import { api, type BracketState } from '../api/client'
import './PositionsTab.css'

interface PositionRow {
  symbol: string
  side: string
  entryPrice: string
  markPrice: string
  positionAmt: string
  leverage: string
  unrealizedProfit: string
}

export function PositionsTab() {
  const [positions, setPositions] = useState<PositionRow[]>([])
  const [brackets, setBrackets] = useState<Record<string, BracketState>>({})
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        setError(null)
        const [pos, bracketData] = await Promise.all([
          api.positions.list(),
          api.autopilot.brackets().catch(() => ({})),
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
          setBrackets(bracketData as Record<string, BracketState>)
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

  const getBracketInfo = (symbol: string) => {
    const b = brackets[symbol]
    if (!b) return { sl: undefined, tp1: undefined, tp2: undefined, tp1Done: false, tp2Done: false, beMoved: false }
    return {
      sl: b.sl_price > 0 ? b.sl_price : undefined,
      tp1: b.tp1_price > 0 ? b.tp1_price : undefined,
      tp2: b.tp2_price > 0 ? b.tp2_price : undefined,
      tp1Done: b.tp1_done,
      tp2Done: b.tp2_done,
      beMoved: b.be_moved,
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
        <p className="positions-sub">SL/TP values are live bracket orders from the engine. Refreshes every 2s.</p>
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
                  <th>TP1</th>
                  <th>TP2</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((row) => {
                  const b = getBracketInfo(row.symbol)
                  const pnl = parseFloat(row.unrealizedProfit)
                  const amt = Math.abs(parseFloat(row.positionAmt))
                  const notional = Number(row.entryPrice) * amt
                  const lev = Number(row.leverage) || 1
                  const margin = lev > 0 ? notional / lev : notional
                  const pnlPct = margin > 0 ? (pnl / margin) * 100 : 0
                  // Status badges
                  const badges: string[] = []
                  if (b.beMoved) badges.push('BE')
                  if (b.tp1Done) badges.push('TP1 Done')
                  if (b.tp2Done) badges.push('TP2 Done')
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
                      <td className={`sl-tp sl-val${b.beMoved ? ' sl-be' : ''}`}>
                        {b.sl != null ? b.sl.toFixed(2) : '—'}
                        {b.beMoved && <span className="be-badge" title="SL moved to breakeven"> BE</span>}
                      </td>
                      <td className={`sl-tp tp-val${b.tp1Done ? ' tp-done' : ''}`}>
                        {b.tp1 != null ? b.tp1.toFixed(2) : '—'}
                      </td>
                      <td className={`sl-tp tp-val${b.tp2Done ? ' tp-done' : ''}`}>
                        {b.tp2 != null ? b.tp2.toFixed(2) : '—'}
                      </td>
                      <td className="bracket-status">
                        {badges.length > 0
                          ? badges.map((badge) => (
                              <span key={badge} className={`bracket-badge bracket-badge--${badge.replace(/\s/g, '').toLowerCase()}`}>
                                {badge}
                              </span>
                            ))
                          : <span className="bracket-badge bracket-badge--active">Active</span>}
                      </td>
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
                  const totalMargin = positions.reduce((s, row) => {
                    const a = Math.abs(parseFloat(row.positionAmt))
                    const n = Number(row.entryPrice) * a
                    const l = Number(row.leverage) || 1
                    return s + (l > 0 ? n / l : n)
                  }, 0)
                  const totalPct = totalMargin > 0 ? (totalPnl / totalMargin) * 100 : 0
                  return (
                    <tr className="positions-total-row">
                      <td colSpan={5}>Total</td>
                      <td>{totalNotional.toFixed(2)}</td>
                      <td>—</td>
                      <td className={totalPnl >= 0 ? 'pnl-pos' : 'pnl-neg'}>
                        {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)}
                        <span className="pnl-pct"> ({totalPct >= 0 ? '+' : ''}{totalPct.toFixed(2)}%)</span>
                      </td>
                      <td colSpan={4} />
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
