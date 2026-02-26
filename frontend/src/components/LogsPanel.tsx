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

  if (loading) {
    return (
      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5">
        <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
          System Logs
        </h3>
        <p className="text-[var(--text-muted)]">Loading...</p>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5">
      <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        System Logs
      </h3>
      <div className="max-h-[400px] overflow-y-auto">
        {logs.length === 0 ? (
          <p className="text-[var(--text-muted)]">No logs</p>
        ) : (
          <div className="space-y-1 font-mono text-xs">
            {logs.map((log) => (
              <div
                key={log.id}
                className="rounded border-l-2 border-[var(--border)] bg-[var(--bg-elevated)] p-2"
              >
                <span className="text-[var(--text-muted)]">{log.ts}</span>{' '}
                <span
                  className={
                    log.level === 'ERROR' || log.level === 'CRITICAL'
                      ? 'text-[var(--negative)]'
                      : 'text-[var(--accent)]'
                  }
                >
                  [{log.level}]
                </span>{' '}
                {log.event}: {log.message}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
