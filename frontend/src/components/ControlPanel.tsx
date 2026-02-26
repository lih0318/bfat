import { useState } from 'react'

interface ControlPanelProps {
  engineState: string
  onStart: () => void
  onStop: () => void
}

export function ControlPanel({ engineState, onStart, onStop }: ControlPanelProps) {
  const [confirmStop, setConfirmStop] = useState(false)
  const isRunning = engineState === 'open' || engineState === 'entering' || engineState === 'closing'

  const handleStopClick = () => {
    if (!confirmStop) {
      setConfirmStop(true)
      return
    }
    onStop()
    setConfirmStop(false)
  }

  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5 shadow-[var(--shadow)] ring-1 ring-white/5 backdrop-blur-sm">
      <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        Control
      </h3>
      <div className="flex flex-wrap gap-3">
        <button
          onClick={onStart}
          disabled={isRunning}
          className="min-h-[44px] rounded-lg bg-[var(--positive)] px-5 py-2.5 font-medium text-white transition-all duration-200 hover:enabled:scale-[1.02] hover:enabled:opacity-95 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:scale-100 touch-manipulation focus:outline-none focus:ring-2 focus:ring-[var(--positive)] focus:ring-offset-2 focus:ring-offset-[#0a0e12]"
        >
          Start Engine
        </button>
        <button
          onClick={handleStopClick}
          className="min-h-[44px] rounded-lg bg-[var(--negative)] px-5 py-2.5 font-medium text-white transition-all duration-200 hover:scale-[1.02] hover:opacity-95 active:scale-[0.98] touch-manipulation focus:outline-none focus:ring-2 focus:ring-[var(--negative)] focus:ring-offset-2 focus:ring-offset-[#0a0e12]"
        >
          {confirmStop ? 'Confirm STOP' : 'Stop Engine'}
        </button>
        {confirmStop && (
          <button
            onClick={() => setConfirmStop(false)}
            className="min-h-[44px] rounded-lg border border-[var(--border)] px-5 py-2.5 font-medium transition-all duration-200 hover:bg-[var(--bg-elevated)] hover:scale-[1.02] active:scale-[0.98] touch-manipulation focus:outline-none focus:ring-2 focus:ring-[var(--accent)] focus:ring-offset-2 focus:ring-offset-[#0a0e12]"
          >
            Cancel
          </button>
        )}
      </div>
    </div>
  )
}
