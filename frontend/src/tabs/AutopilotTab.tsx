import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import './AutopilotTab.css'

interface AutopilotStatus {
  running: boolean
  reason: string
  symbol: string
  max_usdt: number
  max_leverage: number
}

interface ActivityItem {
  ts: string
  type: string
  symbol: string
  message: string
}

export function AutopilotTab() {
  const [status, setStatus] = useState<AutopilotStatus | null>(null)
  const [activity, setActivity] = useState<ActivityItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [activityMode, setActivityMode] = useState<'all' | 'live'>('all')
  const formDirtyRef = useRef(false)
  const [form, setForm] = useState({
    max_usdt: 1000,
    max_leverage: 5,
    daily_loss_limit_usdt: 0,
    reentry_cooldown_minutes: 15,
    symbol: 'BTCUSDT',
    entry_tf: '15m',
    trend_tf: '1h',
    allow_position_flip: true,
    flip_fee_bps: 8,
    flip_slippage_bps: 5,
    flip_min_edge_ratio: 1.5,
  })

  const load = async () => {
    try {
      setError(null)
      const [s, c, a] = await Promise.all([
        api.autopilot.status(),
        api.autopilot.config(),
        api.autopilot.activity(100, activityMode),
      ])
      setStatus(s)
      setActivity(Array.isArray(a) ? a : [])
      if (!formDirtyRef.current) {
        setForm((prev) => applyConfigToForm(c as Record<string, unknown>, prev))
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [activityMode])

  const handleStart = async () => {
    try {
      setError(null)
      await api.autopilot.start()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const handleStop = async () => {
    try {
      setError(null)
      await api.autopilot.stop()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const applyConfigToForm = (config: Record<string, unknown>, prev: typeof form) => ({
    ...prev,
    max_usdt: Number(config.max_usdt ?? prev.max_usdt),
    max_leverage: Number(config.max_leverage ?? prev.max_leverage),
    daily_loss_limit_usdt: Number(config.daily_loss_limit_usdt ?? prev.daily_loss_limit_usdt),
    reentry_cooldown_minutes: Number(config.reentry_cooldown_minutes ?? prev.reentry_cooldown_minutes ?? 15),
    symbol: String(config.symbol ?? prev.symbol),
    entry_tf: String(config.entry_tf ?? prev.entry_tf),
    trend_tf: String(config.trend_tf ?? prev.trend_tf),
    allow_position_flip: Boolean(config.allow_position_flip ?? prev.allow_position_flip),
    flip_fee_bps: Number(config.flip_fee_bps ?? prev.flip_fee_bps),
    flip_slippage_bps: Number(config.flip_slippage_bps ?? prev.flip_slippage_bps),
    flip_min_edge_ratio: Number(config.flip_min_edge_ratio ?? prev.flip_min_edge_ratio),
  })

  const handleSaveConfig = async () => {
    setSaving(true)
    try {
      setError(null)
      const reentry = Math.max(0, Math.min(1440, Math.round(Number(form.reentry_cooldown_minutes)) || 0))
      const res = await api.autopilot.putConfig({
        max_usdt: form.max_usdt,
        max_leverage: form.max_leverage,
        daily_loss_limit_usdt: form.daily_loss_limit_usdt,
        reentry_cooldown_minutes: reentry,
        symbol: form.symbol,
        entry_tf: form.entry_tf,
        trend_tf: form.trend_tf,
        allow_position_flip: form.allow_position_flip,
        flip_fee_bps: form.flip_fee_bps,
        flip_slippage_bps: form.flip_slippage_bps,
        flip_min_edge_ratio: form.flip_min_edge_ratio,
      })
      formDirtyRef.current = false
      if (res.config && typeof res.config === 'object') {
        setForm((prev) => applyConfigToForm(res.config as Record<string, unknown>, prev))
      }
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="richman-tab">
      {error && <p className="richman-error">{error}</p>}
      <div className="richman-hero">
        <div className="richman-mascot" aria-hidden>
          <img src="/richman-character.png" alt="Rich Man" className="richman-character-img" />
        </div>
        <div className="richman-status-card">
          <h3>Status</h3>
          <p className={status?.running ? 'status-running' : 'status-stopped'}>
            {status?.running ? 'Running' : 'Stopped'}
            {status?.reason ? ` (${status.reason})` : ''}
          </p>
          <div className="richman-actions">
            <button type="button" className="btn-start" onClick={handleStart} disabled={status?.running === true}>
              Start
            </button>
            <button type="button" className="btn-stop" onClick={handleStop} disabled={!status?.running}>
              Stop
            </button>
          </div>
        </div>
      </div>
      <div className="richman-top">
        <div className="richman-config-card">
          <h3>Settings</h3>
          <div className="config-sections">
            <section className="config-section">
              <h4>Capital / Leverage</h4>
              <div className="config-row">
                <label>
                  <span>Max USDT</span>
                  <input
                    type="number"
                    min={0}
                    step={100}
                    value={form.max_usdt}
                    onChange={(e) => { formDirtyRef.current = true; setForm((f) => ({ ...f, max_usdt: Number(e.target.value) || 0 })) }}
                  />
                </label>
                <label>
                  <span>Max Leverage</span>
                  <input
                    type="number"
                    min={1}
                    max={125}
                    value={form.max_leverage}
                    onChange={(e) => { formDirtyRef.current = true; setForm((f) => ({ ...f, max_leverage: Number(e.target.value) || 1 })) }}
                  />
                </label>
              </div>
            </section>
            <section className="config-section">
              <h4>Loss Limit &amp; Reentry</h4>
              <div className="config-row">
                <label>
                  <span>Daily Loss Limit (USDT, 0=off)</span>
                  <input
                    type="number"
                    min={0}
                    value={form.daily_loss_limit_usdt}
                    onChange={(e) => { formDirtyRef.current = true; setForm((f) => ({ ...f, daily_loss_limit_usdt: Number(e.target.value) || 0 })) }}
                  />
                </label>
                <label>
                  <span>Reentry cooldown (min, 0=immediate)</span>
                  <input
                    type="number"
                    min={0}
                    max={1440}
                    value={form.reentry_cooldown_minutes}
                    onChange={(e) => { formDirtyRef.current = true; setForm((f) => ({ ...f, reentry_cooldown_minutes: Number(e.target.value) || 0 })) }}
                  />
                </label>
              </div>
            </section>
            <section className="config-section">
              <h4>Trading</h4>
              <div className="config-row config-row--three">
                <label>
                  <span>Symbol</span>
                  <input
                    type="text"
                    value={form.symbol}
                    onChange={(e) => { formDirtyRef.current = true; setForm((f) => ({ ...f, symbol: e.target.value.toUpperCase() })) }}
                  />
                </label>
                <label>
                  <span>Entry TF</span>
                  <input
                    type="text"
                    value={form.entry_tf}
                    onChange={(e) => { formDirtyRef.current = true; setForm((f) => ({ ...f, entry_tf: e.target.value })) }}
                  />
                </label>
                <label>
                  <span>Trend TF</span>
                  <input
                    type="text"
                    value={form.trend_tf}
                    onChange={(e) => { formDirtyRef.current = true; setForm((f) => ({ ...f, trend_tf: e.target.value })) }}
                  />
                </label>
              </div>
            </section>
            <section className="config-section config-section--flip">
              <h4 className="config-subtitle">Position flip (opposite signal)</h4>
              <label className="config-checkbox">
                <input
                  type="checkbox"
                  checked={form.allow_position_flip}
                  onChange={(e) => { formDirtyRef.current = true; setForm((f) => ({ ...f, allow_position_flip: e.target.checked })) }}
                />
                <span>Allow flip when opposite signal (only if cost &lt; upside)</span>
              </label>
              {form.allow_position_flip && (
                <div className="config-flip-extra">
                  <div className="config-row">
                    <label>
                      <span>Fee (bps per leg)</span>
                      <input
                        type="number"
                        min={0}
                        max={100}
                        step={0.5}
                        value={form.flip_fee_bps}
                        onChange={(e) => { formDirtyRef.current = true; setForm((f) => ({ ...f, flip_fee_bps: Number(e.target.value) || 0 })) }}
                      />
                    </label>
                    <label>
                      <span>Slippage (bps per leg)</span>
                      <input
                        type="number"
                        min={0}
                        max={100}
                        step={0.5}
                        value={form.flip_slippage_bps}
                        onChange={(e) => { formDirtyRef.current = true; setForm((f) => ({ ...f, flip_slippage_bps: Number(e.target.value) || 0 })) }}
                      />
                    </label>
                    <label>
                      <span>Min edge ratio (upside vs cost)</span>
                      <input
                        type="number"
                        min={0.5}
                        max={10}
                        step={0.1}
                        value={form.flip_min_edge_ratio}
                        onChange={(e) => { formDirtyRef.current = true; setForm((f) => ({ ...f, flip_min_edge_ratio: Number(e.target.value) || 0 })) }}
                      />
                    </label>
                  </div>
                </div>
              )}
            </section>
          </div>
          <div className="config-footer">
            <button type="button" className="btn-save" onClick={handleSaveConfig} disabled={saving}>
              {saving ? 'Saving...' : 'Save config'}
            </button>
          </div>
        </div>
      </div>
      <div className="richman-activity">
        <h3>Activity log</h3>
        <div className="activity-filters">
          <span className="activity-filter-label">Show:</span>
          {(['all', 'live'] as const).map((m) => (
            <button
              key={m}
              type="button"
              className={`activity-filter-btn ${activityMode === m ? 'active' : ''}`}
              onClick={() => setActivityMode(m)}
            >
              {m === 'all' ? 'All' : 'Live only'}
            </button>
          ))}
        </div>
        <div className="activity-list">
          {activity.length === 0 && <p className="activity-empty">No activity yet.</p>}
          {activity.map((item, i) => (
            <div key={`${item.ts}-${i}`} className={`activity-item activity-${item.type}`}>
              <span className="activity-ts">{new Date(item.ts).toLocaleTimeString()}</span>
              <span className="activity-type">{item.type}</span>
              <span className="activity-symbol">{item.symbol}</span>
              <span className="activity-msg">{item.message}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
