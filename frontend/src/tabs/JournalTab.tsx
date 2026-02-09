import { useCallback, useEffect, useState } from 'react'
import { api, type JournalEntry } from '../api/client'
import './JournalTab.css'

type JournalMode = 'all' | 'live'
type JournalTypeFilter = 'all' | 'entry' | 'exit' | 'paper_entry' | 'paper_exit'

export function JournalTab() {
  const [entries, setEntries] = useState<JournalEntry[]>([])
  const [mode, setMode] = useState<JournalMode>('all')
  const [typeFilter, setTypeFilter] = useState<JournalTypeFilter>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [clearing, setClearing] = useState(false)

  const loadEntries = useCallback(() => {
    setLoading(true)
    setError(null)
    api.journal
      .list(200, mode, typeFilter === 'all' ? undefined : typeFilter)
      .then(setEntries)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [mode, typeFilter])

  useEffect(() => {
    loadEntries()
  }, [loadEntries])

  const handleClearJournal = async () => {
    if (!window.confirm('정말 모든 저널 데이터를 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.')) return
    setClearing(true)
    setError(null)
    try {
      await api.journal.clear()
      await loadEntries()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setClearing(false)
    }
  }

  const formatTs = (ts: string) => {
    try {
      const d = new Date(ts)
      return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'medium' })
    } catch {
      return ts
    }
  }

  const formatNum = (n: number | undefined) =>
    n != null ? n.toLocaleString(undefined, { maximumFractionDigits: 6 }) : '—'

  const typeLabel = (e: JournalEntry) => {
    if (e.type === 'entry') return 'Entry'
    if (e.type === 'paper_entry') return 'Paper Entry'
    if (e.type === 'paper_exit') return 'Paper Exit'
    return 'Exit'
  }

  return (
    <div className="journal-tab">
      <header className="journal-header">
        <h2 className="journal-title">Trading Journal</h2>
        <p className="journal-desc">Entries and exits are logged automatically by Rich Man.</p>
        <div className="journal-filters">
          <span className="journal-filter-label">Show:</span>
          {(['all', 'live'] as const).map((m) => (
            <button
              key={m}
              type="button"
              className={`journal-filter-btn ${mode === m ? 'active' : ''}`}
              onClick={() => setMode(m)}
            >
              {m === 'all' ? 'All' : 'Live only'}
            </button>
          ))}
          <span className="journal-filter-label">Type:</span>
          {(['all', 'entry', 'exit', 'paper_entry', 'paper_exit'] as const).map((t) => (
            <button
              key={t}
              type="button"
              className={`journal-filter-btn ${typeFilter === t ? 'active' : ''}`}
              onClick={() => setTypeFilter(t)}
            >
              {t === 'all' ? 'All' : t === 'entry' ? 'Entry' : t === 'exit' ? 'Exit' : t === 'paper_entry' ? 'Paper Entry' : 'Paper Exit'}
            </button>
          ))}
        </div>
        <div className="journal-actions">
          <button
            type="button"
            className="journal-clear-btn"
            onClick={handleClearJournal}
            disabled={clearing || loading}
          >
            {clearing ? 'Clearing...' : 'Clear Journal Data'}
          </button>
        </div>
      </header>
      {error && <p className="journal-error">{error}</p>}
      {loading && <p className="journal-loading">Loading...</p>}
      {!loading && !error && (
        <div className="journal-table-wrap">
          <table className="journal-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Type</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>Qty</th>
                <th>SL</th>
                <th>TP</th>
                <th>Realized PnL</th>
                <th>잔고 대비 %</th>
              </tr>
            </thead>
            <tbody>
              {entries.length === 0 && (
                <tr>
                  <td colSpan={11} className="journal-empty">
                    No entries yet.
                  </td>
                </tr>
              )}
              {entries.map((e, i) => (
                <tr key={e.id ?? e.ts ?? i} className={`journal-row journal-row--${e.type}`}>
                  <td>{formatTs(e.ts)}</td>
                  <td>
                    <span className={`journal-type journal-type--${e.type}`}>
                      {typeLabel(e)}
                    </span>
                  </td>
                  <td>{e.symbol}</td>
                  <td>{e.side ?? '—'}</td>
                  <td>{formatNum(e.entry_price)}</td>
                  <td>{formatNum(e.exit_price)}</td>
                  <td>{formatNum(e.qty)}</td>
                  <td>{formatNum(e.sl)}</td>
                  <td>{formatNum(e.tp)}</td>
                  <td className={e.realized_pnl != null && e.realized_pnl < 0 ? 'negative' : ''}>
                    {e.realized_pnl != null ? `${e.realized_pnl >= 0 ? '+' : ''}${e.realized_pnl.toFixed(2)}` : '—'}
                  </td>
                  <td className={e.pnl_pct_of_balance != null && e.pnl_pct_of_balance < 0 ? 'negative' : ''}>
                    {e.pnl_pct_of_balance != null
                      ? `${e.pnl_pct_of_balance >= 0 ? '+' : ''}${e.pnl_pct_of_balance.toFixed(2)}%`
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
