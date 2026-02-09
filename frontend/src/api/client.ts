/**
 * API base URL: dev uses proxy (/api), Windows Standalone uses VITE_API_BASE_URL (e.g. http://127.0.0.1:8000)
 */
const BASE = (import.meta.env.VITE_API_BASE_URL as string) || ''

// ── Auth token management ──
const TOKEN_KEY = 'bfat_auth_token'

export function getAuthToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setAuthToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearAuthToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

async function fetchApi<T>(path: string, init?: RequestInit): Promise<T> {
  const url = path.startsWith('http') ? path : `${BASE}${path}`
  const token = getAuthToken()
  const headers = new Headers(init?.headers ?? {})
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  const res = await fetch(url, { ...init, headers, credentials: 'omit' })
  if (res.status === 401) {
    // For auth endpoints (login, check), don't treat 401 as session expiry —
    // just pass the error through so the caller can show the real message.
    const isAuthEndpoint = path.includes('/api/auth/')
    if (!isAuthEndpoint) {
      clearAuthToken()
      window.dispatchEvent(new Event('auth-expired'))
      throw new Error('Session expired. Please log in again.')
    }
    // Auth endpoint 401: return actual error detail from server
    const text = await res.text()
    throw new Error(text || 'Authentication failed')
  }
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

export interface RegimeTf {
  timeframe: string
  adx: number | null
  regime: string
  trend_direction: string
}

export interface MarketRegimeResponse {
  symbol: string
  '1d': RegimeTf
  '1h': RegimeTf
}

export const api = {
  health: () => fetchApi<{ status: string }>('/api/health'),
  auth: {
    login: (username: string, password: string) =>
      fetchApi<{ ok: boolean; token?: string; message?: string }>('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      }),
    check: () =>
      fetchApi<{ ok: boolean; authenticated: boolean }>('/api/auth/check'),
    logout: () =>
      fetchApi<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),
  },
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
    marketRegime: (symbol?: string) => {
      const q = symbol != null && symbol !== '' ? `?symbol=${encodeURIComponent(symbol)}` : ''
      return fetchApi<MarketRegimeResponse>(`/api/autopilot/market-regime${q}`)
    },
  },
  journal: {
    list: (limit?: number, mode?: 'all' | 'live', type?: string) => {
      const p = new URLSearchParams()
      if (limit != null) p.set('limit', String(limit))
      if (mode && mode !== 'all') p.set('mode', mode)
      if (type && type !== 'all') p.set('type', type)
      const q = p.toString()
      return fetchApi<Array<JournalEntry>>(`/api/journal${q ? `?${q}` : ''}`)
    },
    clear: () =>
      fetchApi<{ ok: boolean }>('/api/journal', { method: 'DELETE' }),
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
