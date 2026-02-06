/**
 * API base URL: dev uses proxy (/api), Windows Standalone uses VITE_API_BASE_URL (e.g. http://127.0.0.1:8000)
 */
const BASE = (import.meta.env.VITE_API_BASE_URL as string) || ''

async function fetchApi<T>(path: string, init?: RequestInit): Promise<T> {
  const url = path.startsWith('http') ? path : `${BASE}${path}`
  const res = await fetch(url, { ...init, credentials: 'omit' })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => fetchApi<{ status: string }>('/api/health'),
  account: {
    balance: () => fetchApi<Array<Record<string, unknown>>>('/api/account/balance'),
    account: () => fetchApi<Record<string, unknown>>('/api/account/account'),
  },
  klines: (params: { symbol: string; interval?: string; limit?: number }) => {
    const sp = new URLSearchParams()
    sp.set('symbol', params.symbol)
    sp.set('interval', params.interval ?? '15m')
    if (params.limit != null) sp.set('limit', String(params.limit))
    return fetchApi<Array<{ time: number; open: number; high: number; low: number; close: number; volume: number }>>(
      `/api/klines?${sp}`
    )
  },
  positions: {
    list: (symbol?: string) => {
      const sp = symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''
      return fetchApi<Array<Record<string, unknown>>>(`/api/positions${sp}`)
    },
    openOrders: (symbol: string) =>
      fetchApi<Array<Record<string, unknown>>>(`/api/positions/open-orders?symbol=${encodeURIComponent(symbol)}`),
    close: (symbol: string) =>
      fetchApi<{ ok: boolean; message?: string }>(
        `/api/positions/close?symbol=${encodeURIComponent(symbol)}`,
        { method: 'POST' }
      ),
  },
  autopilot: {
    config: () => fetchApi<Record<string, unknown>>('/api/autopilot/config'),
    putConfig: (body: Record<string, unknown>) =>
      fetchApi<{ ok: boolean; config?: Record<string, unknown> }>('/api/autopilot/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    start: () => fetchApi<{ ok: boolean; message?: string }>('/api/autopilot/start', { method: 'POST' }),
    stop: () => fetchApi<{ ok: boolean; message?: string }>('/api/autopilot/stop', { method: 'POST' }),
    status: () =>
      fetchApi<{
        running: boolean
        reason: string
        symbol: string
        max_usdt: number
        max_leverage: number
      }>('/api/autopilot/status'),
    activity: (limit?: number, mode?: 'all' | 'live') => {
      const p = new URLSearchParams()
      if (limit != null) p.set('limit', String(limit))
      if (mode && mode !== 'all') p.set('mode', mode)
      const q = p.toString()
      return fetchApi<Array<{ ts: string; type: string; symbol: string; message: string }>>(
        `/api/autopilot/activity${q ? `?${q}` : ''}`
      )
    },
  },
  journal: {
    list: (limit?: number, mode?: 'all' | 'live') => {
      const p = new URLSearchParams()
      if (limit != null) p.set('limit', String(limit))
      if (mode && mode !== 'all') p.set('mode', mode)
      const q = p.toString()
      return fetchApi<Array<JournalEntry>>(`/api/journal${q ? `?${q}` : ''}`)
    },
  },
  paper: {
    status: () =>
      fetchApi<{ balance: number; initial_balance: number; positions: Array<PaperPosition> }>('/api/paper/status'),
    reset: () =>
      fetchApi<{ balance: number; initial_balance: number; positions: unknown[] }>('/api/paper/reset', { method: 'POST' }),
    close: (symbol: string) =>
      fetchApi<{ ok: boolean; realized_pnl?: number; message?: string }>('/api/paper/close', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol }),
      }),
  },
}

export interface JournalEntry {
  id?: string
  ts: string
  type: 'entry' | 'exit' | 'paper_entry' | 'paper_exit'
  symbol: string
  side?: string
  entry_price?: number
  exit_price?: number
  qty?: number
  sl?: number
  tp?: number
  realized_pnl?: number
  /** Realized PnL as % of account balance before the trade (exit entries only) */
  pnl_pct_of_balance?: number
  client_order_id?: string
}
