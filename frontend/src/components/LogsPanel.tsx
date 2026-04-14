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

const kstFormatter = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

function formatKst(utcIso: string): string {
  try {
    const date = new Date(utcIso.endsWith('Z') ? utcIso : utcIso + 'Z')
    if (isNaN(date.getTime())) return utcIso
    return kstFormatter.format(date) + ' KST'
  } catch {
    return utcIso
  }
}

const LEVEL_STYLES: Record<string, { border: string; bg: string; text: string }> = {
  ERROR: { border: 'border-l-[var(--negative)]', bg: 'bg-[var(--negative-muted)]', text: 'text-[var(--negative)]' },
  CRITICAL: { border: 'border-l-[var(--negative)]', bg: 'bg-[var(--negative-muted)]', text: 'text-[var(--negative)]' },
  WARNING: { border: 'border-l-[var(--warning)]', bg: 'bg-[var(--warning-muted)]', text: 'text-[var(--warning)]' },
}
const DEFAULT_STYLE = { border: 'border-l-[var(--accent)]/40', bg: 'bg-[var(--accent-muted)]/40', text: 'text-[var(--accent)]' }

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
      } catch { setLogs([]) }
      finally { setLoading(false) }
    }
    fetchLogs()
    const interval = setInterval(fetchLogs, 15000)
    return () => clearInterval(interval)
  }, [accessToken])

  if (loading) {
    return (
      <div className="card p-5">
        <p className="section-title mb-4">System Logs</p>
        <div className="flex items-center gap-3 text-[var(--text-muted)]">
          <div className="h-5 w-5 animate-spin-slow rounded-full border-2 border-[var(--border)] border-t-[var(--accent)]" />
          <span className="text-sm">Loading...</span>
        </div>
      </div>
    )
  }

  return (
    <div className="card p-5">
      <p className="section-title mb-4">System Logs</p>
      <div className="max-h-[500px] overflow-y-auto pr-1">
        {logs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-[var(--bg-elevated)]">
              <svg className="h-6 w-6 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <p className="text-sm text-[var(--text-muted)]">로그가 없습니다</p>
            <p className="mt-1 text-xs text-[var(--text-muted)]/70">엔진이 실행되면 여기에 표시됩니다</p>
          </div>
        ) : (
          <div className="space-y-1 font-mono text-xs">
            {logs.map((log) => {
              const style = LEVEL_STYLES[log.level] ?? DEFAULT_STYLE
              return (
                <div key={log.id} className={`rounded-r-lg border-l-[3px] px-3 py-2 ${style.border} ${style.bg}`}>
                  <span className="tabular-nums text-[var(--text-muted)]">{formatKst(log.ts)}</span>{' '}
                  <span className={`font-semibold ${style.text}`}>[{log.level}]</span>{' '}
                  <span className="font-medium text-[var(--text-secondary)]">{log.event}</span>
                  <span className="text-[var(--text-muted)]">: {log.message}</span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
