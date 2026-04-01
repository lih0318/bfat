import { useState } from 'react'

interface ControlPanelProps {
  engineState: string
  startLoading: boolean
  stopLoading: boolean
  controlError: string | null
  onStart: () => void
  onStop: () => void
}

export function ControlPanel({
  engineState,
  startLoading,
  stopLoading,
  controlError,
  onStart,
  onStop,
}: ControlPanelProps) {
  const [confirmStop, setConfirmStop] = useState(false)
  const isRunning = engineState !== 'stopped'

  const handleStopClick = () => {
    if (!confirmStop) { setConfirmStop(true); return }
    onStop()
    setConfirmStop(false)
  }

  return (
    <div className="card p-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="section-title">Engine Control</p>
          {isRunning && (
            <p className="mt-1.5 text-sm text-[var(--positive)]">엔진 실행 중 &middot; 시그널 대기</p>
          )}
          {controlError && (
            <p className="mt-1.5 text-sm text-[var(--negative)]">{controlError}</p>
          )}
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={onStart}
            disabled={isRunning || startLoading}
            className="btn-primary bg-[var(--positive)] text-white px-6"
          >
            {startLoading ? (
              <span className="flex items-center gap-2">
                <span className="h-4 w-4 animate-spin-slow rounded-full border-2 border-white/30 border-t-white" />
                Starting...
              </span>
            ) : 'Start Engine'}
          </button>
          <button
            onClick={handleStopClick}
            disabled={!isRunning || stopLoading}
            className="btn-primary bg-[var(--negative)] text-white px-6"
          >
            {stopLoading ? 'Stopping...' : confirmStop ? 'Confirm STOP' : 'Stop Engine'}
          </button>
          {confirmStop && (
            <button
              onClick={() => setConfirmStop(false)}
              className="btn-primary border border-[var(--border)] bg-transparent text-[var(--text-secondary)] px-5"
            >
              Cancel
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
