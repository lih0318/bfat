import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { setAuthFailureCallback, setTokenRefreshedCallback } from '../api/client'

interface AuthContextValue {
  isAuthenticated: boolean
  isLoading: boolean
  username: string | null
  accessToken: string | null
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  setAccessToken: (token: string | null) => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [username, setUsername] = useState<string | null>(null)
  const [accessToken, setAccessToken] = useState<string | null>(null)

  const logout = useCallback(async () => {
    try {
      await fetch('/api/logout', { method: 'POST', credentials: 'include' })
    } catch {
      // ignore
    }
    setAccessToken(null)
    setUsername(null)
    setIsAuthenticated(false)
  }, [])

  useEffect(() => {
    setAuthFailureCallback(() => {
      setAccessToken(null)
      setUsername(null)
      setIsAuthenticated(false)
    })
    setTokenRefreshedCallback((token) => setAccessToken(token))
    return () => {
      setAuthFailureCallback(null)
      setTokenRefreshedCallback(null)
    }
  }, [])

  const refreshIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    let cancelled = false
    async function check() {
      const res = await fetch('/api/refresh', {
        method: 'POST',
        credentials: 'include',
      })
      if (cancelled) return
      if (res.ok) {
        const data = await res.json()
        const token = data.access_token
        if (token) {
          setAccessToken(token)
          setIsAuthenticated(true)
          const meRes = await fetch('/api/me', {
            headers: { Authorization: `Bearer ${token}` },
            credentials: 'include',
          })
          if (meRes.ok) {
            const me = await meRes.json()
            setUsername(me.username ?? null)
          }
        }
      }
      setIsLoading(false)
    }
    check()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!isAuthenticated) return
    refreshIntervalRef.current = setInterval(async () => {
      const res = await fetch('/api/refresh', {
        method: 'POST',
        credentials: 'include',
      })
      if (res.ok) {
        const data = await res.json()
        const token = data.access_token
        if (token) setAccessToken(token)
      }
    }, 25 * 60 * 1000)
    return () => {
      if (refreshIntervalRef.current) {
        clearInterval(refreshIntervalRef.current)
        refreshIntervalRef.current = null
      }
    }
  }, [isAuthenticated])

  const login = useCallback(async (u: string, p: string) => {
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ username: u, password: p }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail ?? 'Login failed')
    }
    const data = await res.json()
    const token = data.access_token
    if (!token) throw new Error('No token in response')
    setAccessToken(token)
    setUsername(u)
    setIsAuthenticated(true)
  }, [])

  const value: AuthContextValue = {
    isAuthenticated,
    isLoading,
    username,
    accessToken,
    login,
    logout,
    setAccessToken,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
