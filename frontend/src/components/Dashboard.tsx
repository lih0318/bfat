import { useEffect, useState } from 'react'
import { ControlPanel } from './ControlPanel'
import { LogsPanel } from './LogsPanel'
import { PositionCard, type PositionData } from './PositionCard'

interface StatusData {
  engine_state: string
  position: Record<string, unknown> | null
  last_signal: Record<string, string> | null
  current_stop_price: number | null
  equity: number
  kill_switch_triggered: boolean
  error: string | null
}

const WS_URL = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/status`

export function Dashboard() {
  const [status, setStatus] = useState<StatusData | null>(null)
  const [activeTab, setActiveTab] = useState<'dashboard' | 'logs' | 'system'>('dashboard')

  useEffect(() => {
    let ws: WebSocket | null = null
    const connect = () => {
      ws = new WebSocket(WS_URL)
      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data)
          setStatus(data)
        } catch {
          // ignore
        }
      }
      ws.onclose = () => {
        setTimeout(connect, 3000)
      }
    }
    connect()
    return () => {
      ws?.close()
    }
  }, [])

  const engineState = status?.engine_state ?? 'stopped'
  const isRunning = engineState === 'open' || engineState === 'entering' || engineState === 'closing'
  const displayState = isRunning ? 'RUNNING' : 'STOPPED'

  const handleStart = async () => {
    await fetch('/api/start', { method: 'POST' })
  }

  const handleStop = async () => {
    await fetch('/api/stop', { method: 'POST' })
  }

  return (
    <div className="min-h-screen bg-[#0f1419] text-[var(--text)]">
      <header className="border-b border-[var(--border)] bg-[var(--bg-card)] px-4 py-4 md:px-6">
        <div className="mx-auto flex max-w-6xl flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-xl font-bold text-[var(--accent)]">BFAT</h1>
            <p className="text-sm text-[var(--text-muted)]">Bitcoin Futures Auto Trader</p>
          </div>
          <div className="flex flex-wrap items-center gap-4">
            <div
              className={`rounded-lg px-4 py-2 font-semibold ${
                isRunning ? 'bg-[var(--positive)]/20 text-[var(--positive)]' : 'bg-[var(--border)]/30 text-[var(--text-muted)]'
              }`}
            >
              {displayState}
            </div>
            <div className="rounded-lg border border-[var(--border)] px-4 py-2">
              <span className="text-xs text-[var(--text-muted)]">Equity</span>
              <p className="font-medium">{status?.equity != null ? status.equity.toFixed(2) : '–'} USDT</p>
            </div>
            {status?.kill_switch_triggered && (
              <div className="rounded-lg bg-[var(--negative)]/20 px-4 py-2 text-[var(--negative)] font-semibold">
                KILL SWITCH
              </div>
            )}
            {status?.error && (
              <div className="rounded-lg bg-[var(--negative)]/20 px-4 py-2 text-[var(--negative)]">
                CRITICAL
              </div>
            )}
          </div>
        </div>
      </header>

      <nav className="border-b border-[var(--border)] bg-[var(--bg-elevated)]">
        <div className="mx-auto flex max-w-6xl gap-0">
          {(['dashboard', 'logs', 'system'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`min-h-[48px] flex-1 px-4 font-medium capitalize transition touch-manipulation md:flex-none md:px-6 ${
                activeTab === tab
                  ? 'border-b-2 border-[var(--accent)] text-[var(--accent)]'
                  : 'text-[var(--text-muted)] hover:text-white'
              }`}
            >
              {tab === 'system' ? 'System Info' : tab}
            </button>
          ))}
        </div>
      </nav>

      <main className="mx-auto max-w-6xl flex-1 p-4 md:p-6">
        {activeTab === 'dashboard' && (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            <div className="md:col-span-2 lg:col-span-3">
              <ControlPanel engineState={engineState} onStart={handleStart} onStop={handleStop} />
            </div>
            <div className="lg:col-span-2">
              <PositionCard
                position={(status?.position as PositionData | null) ?? null}
                currentStopPrice={status?.current_stop_price ?? null}
                rMultiple={null}
              />
            </div>
            <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5">
              <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                Last Signal
              </h3>
              {status?.last_signal ? (
                <div className="space-y-2">
                  <p><span className="text-[var(--text-muted)]">Symbol:</span> {status.last_signal.symbol}</p>
                  <p><span className="text-[var(--text-muted)]">Side:</span> {status.last_signal.side}</p>
                  <p className="text-xs text-[var(--text-muted)]">{status.last_signal.signal_candle_ts || status.last_signal.signal_time}</p>
                </div>
              ) : (
                <p className="text-[var(--text-muted)]">No signal</p>
              )}
            </div>
          </div>
        )}

        {activeTab === 'logs' && (
          <div className="space-y-4">
            <LogsPanel />
          </div>
        )}

        {activeTab === 'system' && (
          <div className="grid gap-4 md:grid-cols-2">
            <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5">
              <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                Engine State
              </h3>
              <p className="font-medium capitalize">{engineState}</p>
            </div>
            <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5">
              <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                Status
              </h3>
              <pre className="max-h-[300px] overflow-auto text-xs">{JSON.stringify(status ?? {}, null, 2)}</pre>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
