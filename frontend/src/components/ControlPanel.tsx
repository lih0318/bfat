import { useState } from 'react'

export type StrategyMode = 'TRENDING' | 'RANGING'

export interface StrategyPreset {
  label: string
  [key: string]: string | number
}

export interface StrategyConfig {
  mode: StrategyMode
  running: boolean
  can_update: boolean
  presets: Record<StrategyMode, StrategyPreset>
}

interface ControlPanelProps {
  engineState: string
  startLoading: boolean
  stopLoading: boolean
  controlError: string | null
  strategyConfig: StrategyConfig | null
  strategyLoading: boolean
  strategyError: string | null
  onStart: () => void
  onStop: () => void
  onStrategyModeChange: (mode: StrategyMode) => void
}

export function ControlPanel({
  engineState,
  startLoading,
  stopLoading,
  controlError,
  strategyConfig,
  strategyLoading,
  strategyError,
  onStart,
  onStop,
  onStrategyModeChange,
}: ControlPanelProps) {
  const [confirmStop, setConfirmStop] = useState(false)
  const isRunning = engineState !== 'stopped'
  const selectedMode = strategyConfig?.mode ?? 'TRENDING'

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

      <div className="mt-5 border-t border-[var(--border-subtle)] pt-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="section-title">Strategy Preset</p>
            {strategyError && (
              <p className="mt-1.5 text-sm text-[var(--negative)]">{strategyError}</p>
            )}
            {isRunning && (
              <p className="mt-1.5 text-xs text-[var(--text-muted)]">Stop engine before changing preset.</p>
            )}
          </div>
          <div className="grid w-full gap-3 md:w-auto md:grid-cols-2">
            {(['TRENDING', 'RANGING'] as StrategyMode[]).map((mode) => {
              const preset = strategyConfig?.presets?.[mode]
              const active = selectedMode === mode
              return (
                <button
                  key={mode}
                  onClick={() => onStrategyModeChange(mode)}
                  disabled={isRunning || strategyLoading || active}
                  className={`min-w-[230px] rounded-lg border px-4 py-3 text-left transition-colors ${
                    active
                      ? 'border-[var(--accent)] bg-[var(--accent-muted)] text-[var(--accent)]'
                      : 'border-[var(--border-subtle)] bg-[var(--bg-elevated)] text-[var(--text-secondary)] hover:border-[var(--border)]'
                  } disabled:cursor-not-allowed disabled:opacity-70`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm font-semibold">{preset?.label ?? mode}</span>
                    {active && <span className="text-[10px] font-bold uppercase">Selected</span>}
                  </div>
                  <div className="mt-2 space-y-1 text-[11px] text-[var(--text-muted)]">
                    {mode === 'TRENDING' ? (
                      <>
                        <p>EMA {preset?.ema_fast ?? 12}/{preset?.ema_slow ?? 50} · ATR {preset?.atr_period ?? 14}</p>
                        <p>SL {preset?.stop_loss ?? '1.8 ATR'} · TP {preset?.take_profit ?? '3.6 ATR'}</p>
                        <p>Volume ≥ {preset?.volume_ratio_min ?? 1.1}x · Risk {preset?.risk_percent ?? 1}%</p>
                      </>
                    ) : (
                      <>
                        <p>Range {preset?.range_lookback ?? 48} bars · RSI {preset?.rsi_period ?? 14}</p>
                        <p>Long RSI &lt; {preset?.rsi_long_below ?? 35} · Short RSI &gt; {preset?.rsi_short_above ?? 65}</p>
                        <p>Stop {preset?.stop_buffer ?? 'max(0.6%, 0.7 ATR)'} · RR ≥ {preset?.minimum_reward_risk ?? 1.1}</p>
                      </>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
