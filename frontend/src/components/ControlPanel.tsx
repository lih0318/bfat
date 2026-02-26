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
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-4 md:p-5">
      <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        Control
      </h3>
      <div className="flex flex-wrap gap-3">
        <button
          onClick={onStart}
          disabled={isRunning}
          className="min-h-[44px] rounded-lg bg-[var(--positive)] px-5 py-2.5 font-medium text-white transition hover:enabled:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 touch-manipulation"
        >
          Start Engine
        </button>
        <button
          onClick={handleStopClick}
          className="min-h-[44px] rounded-lg bg-[var(--negative)] px-5 py-2.5 font-medium text-white transition hover:opacity-90 touch-manipulation"
        >
          {confirmStop ? 'Confirm STOP' : 'Stop Engine'}
        </button>
        {confirmStop && (
          <button
            onClick={() => setConfirmStop(false)}
            className="min-h-[44px] rounded-lg border border-[var(--border)] px-5 py-2.5 font-medium transition hover:bg-[var(--bg-elevated)] touch-manipulation"
          >
            Cancel
          </button>
        )}
      </div>
    </div>
  )
}
