import { useState } from 'react'
import { useAuth } from '../context/AuthContext'

export function LoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const { login } = useAuth()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(username, password)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[var(--bg-base)] px-4">

      {/* Background ambient orbs */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -left-32 -top-32 h-96 w-96 rounded-full bg-[var(--accent)] opacity-[0.03] blur-[120px]" />
        <div className="absolute -bottom-48 -right-24 h-[500px] w-[500px] rounded-full bg-[var(--wealth-green)] opacity-[0.025] blur-[140px]" />
        <div className="absolute left-1/2 top-1/3 h-64 w-64 -translate-x-1/2 rounded-full bg-[var(--burgundy)] opacity-[0.02] blur-[100px]" />
      </div>

      {/* Auth Card */}
      <div className="relative w-full max-w-[400px] animate-fade-in">
        <div className="card p-8 md:p-10 shadow-[var(--shadow-lg)]">

          {/* Brand */}
          <div className="mb-8 text-center">
            <h1 className="text-2xl font-bold tracking-tight text-[var(--accent)]">BFAT</h1>
            <p className="mt-1.5 text-sm text-[var(--text-muted)]">Bitcoin Futures Auto Trader</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label htmlFor="username" className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                Username
              </label>
              <input
                id="username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
                className="w-full rounded-xl border border-[var(--border)] bg-[var(--bg-elevated)] px-4 py-3 text-[var(--text)] transition-all placeholder:text-[var(--text-muted)]/50 focus:border-[var(--accent)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]/30"
                placeholder="Admin"
              />
            </div>
            <div>
              <label htmlFor="password" className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                Password
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
                className="w-full rounded-xl border border-[var(--border)] bg-[var(--bg-elevated)] px-4 py-3 text-[var(--text)] transition-all placeholder:text-[var(--text-muted)]/50 focus:border-[var(--accent)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]/30"
              />
            </div>

            {error && (
              <div className="rounded-lg bg-[var(--negative-muted)] px-4 py-2.5 text-sm text-[var(--negative)]">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="btn-primary w-full bg-[var(--accent)] text-[var(--bg-base)] font-bold"
            >
              {loading ? (
                <span className="flex items-center gap-2">
                  <span className="h-4 w-4 animate-spin-slow rounded-full border-2 border-[var(--bg-base)]/30 border-t-[var(--bg-base)]" />
                  Signing in...
                </span>
              ) : 'Sign in'}
            </button>
          </form>
        </div>

        {/* Footer note */}
        <p className="mt-6 text-center text-[10px] text-[var(--text-muted)]">
          Secured connection &middot; v2
        </p>
      </div>
    </div>
  )
}
