import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { useAuth } from '../context/AuthContext'

interface LogEntry {
  id: number
  ts: string
  level: string
  event: string
  message: string
  payload: string | null
  correlation_id: string | null
}

export function LogsPanel() {
  const { accessToken } = useAuth()
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchLogs = async () => {
      try {
        const res = await apiFetch('/api/logs?limit=50', { token: accessToken })
        const data = await res.json()
        setLogs(Array.isArray(data) ? data : [])
      } catch {
        setLogs([])
      } finally {
        setLoading(false)
      }
    }
    fetchLogs()
    const interval = setInterval(fetchLogs, 5000)
    return () => clearInterval(interval)
  }, [accessToken])

  const getLogLevelStyle = (level: string) => {
    if (level === 'ERROR' || level === 'CRITICAL') {
      return 'border-l-[var(--negative)] bg-[var(--negative)]/5'
    }
    if (level === 'WARNING') {
      return 'border-l-amber-500 bg-amber-500/5'
    }
    return 'border-l-[var(--accent)] bg-[var(--accent)]/5'
  }

  const getLogLevelTextClass = (level: string) => {
    if (level === 'ERROR' || level === 'CRITICAL') return 'text-[var(--negative)]'
    if (level === 'WARNING') return 'text-amber-400'
    return 'text-[var(--accent)]'
  }

  if (loading) {
    return (
      <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5 shadow-[var(--shadow)] ring-1 ring-white/5 backdrop-blur-sm">
        <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
          System Logs
        </h3>
        <div className="flex items-center gap-3 text-[var(--text-muted)]">
          <div className="h-5 w-5 animate-spin-slow rounded-full border-2 border-[var(--border)] border-t-[var(--accent)]" />
          <span className="text-sm">Loading...</span>
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5 shadow-[var(--shadow)] ring-1 ring-white/5 backdrop-blur-sm">
      <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        System Logs
      </h3>
      <div className="max-h-[400px] overflow-y-auto pr-1">
        {logs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <svg className="mb-3 h-12 w-12 text-[var(--text-muted)]/40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <p className="text-sm text-[var(--text-muted)]">로그가 없습니다</p>
            <p className="mt-1 text-xs text-[var(--text-muted)]/80">엔진이 실행되면 여기에 표시됩니다</p>
          </div>
        ) : (
          <div className="space-y-1.5 font-mono text-xs">
            {logs.map((log) => (
              <div
                key={log.id}
                className={`rounded-r border-l-4 px-3 py-2 ${getLogLevelStyle(log.level)}`}
              >
                <span className="tabular-nums text-[var(--text-muted)]">{log.ts}</span>{' '}
                <span className={`font-medium ${getLogLevelTextClass(log.level)}`}>
                  [{log.level}]
                </span>{' '}
                <span className="font-medium">{log.event}</span>: {log.message}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
