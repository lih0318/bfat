import { useEffect, useState } from 'react'
import { api, type InsightData, type SignalItem } from '../api/client'
import './InsightTab.css'

export function InsightTab() {
  const [data, setData] = useState<InsightData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [signalFilter, setSignalFilter] = useState<'all' | 'long' | 'short' | 'deadzone'>('all')
  const [now, setNow] = useState(Date.now())

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        setError(null)
        const insight = await api.autopilot.insight()
        if (!cancelled) setData(insight)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }
    load()
    const t = setInterval(load, 5000) // refresh every 5s
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [])

  // Update local clock every second for countdown timers
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  if (error) {
    return (
      <div className="insight-tab">
        <p className="insight-error">{error}</p>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="insight-tab">
        <p className="insight-loading">로딩 중...</p>
      </div>
    )
  }

  const pulse = data.engine_pulse
  const market = data.market_summary
  const risk = data.risk_status
  const universe = data.universe_scan
  const signals = data.signals
  const portfolio = data.portfolio

  // Filter signals
  const filteredSignals = signals.filter((s) => {
    if (signalFilter === 'all') return true
    if (signalFilter === 'long') return s.final_score > 0
    if (signalFilter === 'short') return s.final_score < 0
    if (signalFilter === 'deadzone') return s.final_score === 0
    return true
  }).sort((a, b) => Math.abs(b.final_score) - Math.abs(a.final_score))

  // Format time duration
  const formatDuration = (sec: number) => {
    if (sec < 60) return `${Math.round(sec)}초`
    const min = Math.floor(sec / 60)
    const s = Math.round(sec % 60)
    if (min < 60) return `${min}분 ${s}초`
    const h = Math.floor(min / 60)
    const m = min % 60
    return `${h}시간 ${m}분`
  }

  // Drawdown gauge percentage
  const drawdownPct = (risk.drawdown_pct / risk.drawdown_threshold) * 100
  const drawdownColor = drawdownPct >= 90 ? '#ef4444' : drawdownPct >= 50 ? '#f59e0b' : '#22c55e'

  // Leverage gauge percentage
  const leveragePct = (risk.gross_leverage / risk.max_leverage) * 100
  const leverageColor = leveragePct >= 90 ? '#ef4444' : leveragePct >= 70 ? '#f59e0b' : '#22c55e'

  return (
    <div className="insight-tab">
      {/* Engine Pulse */}
      <section className="insight-card pulse-card">
        <h2>Engine Pulse (엔진 심장박동)</h2>
        <div className="pulse-grid">
          <div className="pulse-item">
            <span className="pulse-label">마지막 Signal Tick</span>
            <span className="pulse-value">{formatDuration(pulse.time_since_signal_sec)} 전</span>
          </div>
          <div className="pulse-item">
            <span className="pulse-label">다음 Signal Tick</span>
            <span className="pulse-value pulse-countdown">{formatDuration(pulse.next_signal_sec)} 후</span>
          </div>
          <div className="pulse-item">
            <span className="pulse-label">마지막 Exec Tick</span>
            <span className="pulse-value">{formatDuration(pulse.time_since_exec_sec)} 전</span>
          </div>
          <div className="pulse-item">
            <span className="pulse-label">다음 Exec Tick</span>
            <span className="pulse-value pulse-countdown">{formatDuration(pulse.next_exec_sec)} 후</span>
          </div>
          <div className="pulse-item">
            <span className="pulse-label">Signal 사이클</span>
            <span className="pulse-value">#{pulse.signal_count}</span>
          </div>
          <div className="pulse-item">
            <span className="pulse-label">Exec 사이클</span>
            <span className="pulse-value">#{pulse.exec_count}</span>
          </div>
        </div>
      </section>

      <div className="insight-row">
        {/* Market Overview */}
        <section className="insight-card market-card">
          <h2>Market Overview (시장 온도계)</h2>
          <div className="market-temp">
            <span className="market-temp-label">시장 온도</span>
            <span className="market-temp-value">{market.temperature}</span>
          </div>
          <div className="market-sentiment">
            <div className="sentiment-bar">
              <div className="sentiment-bull" style={{ width: `${(market.bullish_count / (market.bullish_count + market.bearish_count + market.neutral_count)) * 100}%` }} />
              <div className="sentiment-neutral" style={{ width: `${(market.neutral_count / (market.bullish_count + market.bearish_count + market.neutral_count)) * 100}%` }} />
              <div className="sentiment-bear" style={{ width: `${(market.bearish_count / (market.bullish_count + market.bearish_count + market.neutral_count)) * 100}%` }} />
            </div>
            <div className="sentiment-labels">
              <span className="sentiment-label-bull">Bull: {market.bullish_count}</span>
              <span className="sentiment-label-neutral">Neutral: {market.neutral_count}</span>
              <span className="sentiment-label-bear">Bear: {market.bearish_count}</span>
            </div>
          </div>
          <div className="market-universe">
            <p>Universe: <strong>{universe.selected_count}/{universe.total_scanned}</strong> 선정</p>
            <p>평균 TrendScore: <strong>{market.avg_trend_score.toFixed(4)}</strong></p>
          </div>
        </section>

        {/* Risk Monitor */}
        <section className="insight-card risk-card">
          <h2>Risk Monitor (리스크 모니터)</h2>
          <div className="risk-item">
            <div className="risk-label">
              <span>Drawdown</span>
              <span className="risk-value">{(risk.drawdown_pct * 100).toFixed(2)}% / {(risk.drawdown_threshold * 100).toFixed(0)}%</span>
            </div>
            <div className="risk-gauge">
              <div className="risk-gauge-fill" style={{ width: `${Math.min(100, drawdownPct)}%`, backgroundColor: drawdownColor }} />
            </div>
          </div>
          <div className="risk-item">
            <div className="risk-label">
              <span>Leverage</span>
              <span className="risk-value">{risk.gross_leverage.toFixed(2)}x / {risk.max_leverage.toFixed(1)}x</span>
            </div>
            <div className="risk-gauge">
              <div className="risk-gauge-fill" style={{ width: `${Math.min(100, leveragePct)}%`, backgroundColor: leverageColor }} />
            </div>
          </div>
          {risk.warnings.length > 0 && (
            <div className="risk-warnings">
              {risk.warnings.map((w, i) => (
                <p key={i} className="risk-warning">⚠ {w}</p>
              ))}
            </div>
          )}
          {risk.warnings.length === 0 && (
            <p className="risk-ok">✓ 경고 없음</p>
          )}
          <div className="risk-kill-status">
            {risk.kill_active ? (
              <span className="kill-status kill-active">Kill Switch 발동</span>
            ) : (
              <span className="kill-status kill-normal">Kill Switch 정상</span>
            )}
          </div>
        </section>
      </div>

      {/* Signal Board */}
      <section className="insight-card signal-board-card">
        <div className="signal-board-header">
          <h2>Signal Board (시그널 보드)</h2>
          <div className="signal-filters">
            {(['all', 'long', 'short', 'deadzone'] as const).map((f) => (
              <button
                key={f}
                type="button"
                className={`signal-filter-btn ${signalFilter === f ? 'active' : ''}`}
                onClick={() => setSignalFilter(f)}
              >
                {f === 'all' ? 'All' : f === 'long' ? 'Long' : f === 'short' ? 'Short' : 'Deadzone'}
              </button>
            ))}
          </div>
        </div>
        <div className="signal-board-table-wrap">
          <table className="signal-board-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Final Score</th>
                <th>Horizons</th>
                <th>RSI</th>
                <th>Funding</th>
                <th>Vol</th>
                <th>판단</th>
              </tr>
            </thead>
            <tbody>
              {filteredSignals.length === 0 && (
                <tr>
                  <td colSpan={7} className="signal-empty">시그널 없음</td>
                </tr>
              )}
              {filteredSignals.map((s) => {
                const horizonStr = Object.entries(s.horizons)
                  .sort(([a], [b]) => Number(a) - Number(b))
                  .map(([h, v]) => (v > 0 ? '↑' : v < 0 ? '↓' : '→'))
                  .join(' ')
                const scoreClass = s.final_score > 0 ? 'score-long' : s.final_score < 0 ? 'score-short' : 'score-neutral'
                return (
                  <tr key={s.symbol}>
                    <td className="signal-symbol">{s.symbol}</td>
                    <td className={scoreClass}>{s.final_score > 0 ? '+' : ''}{s.final_score.toFixed(4)}</td>
                    <td className="signal-horizons">{horizonStr}</td>
                    <td>{s.rsi.toFixed(1)}</td>
                    <td>{(s.funding_rate * 100).toFixed(4)}%</td>
                    <td>{(s.realized_vol * 100).toFixed(1)}%</td>
                    <td className="signal-reasoning">{s.reasoning}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Decision Log */}
      <section className="insight-card decision-card">
        <h2>Decision Log (판단 근거)</h2>
        {portfolio.length === 0 && (
          <p className="decision-empty">현재 포트폴리오가 비어 있습니다.</p>
        )}
        {portfolio.length > 0 && (
          <div className="decision-list">
            {portfolio.map((p) => {
              const signal = signals.find((s) => s.symbol === p.symbol)
              const rank = signals
                .sort((a, b) => Math.abs(b.final_score) - Math.abs(a.final_score))
                .findIndex((s) => s.symbol === p.symbol) + 1
              return (
                <div key={p.symbol} className="decision-item">
                  <div className="decision-header">
                    <span className={`decision-symbol ${p.side === 'LONG' ? 'side-long' : 'side-short'}`}>
                      ● {p.symbol} {p.side}
                    </span>
                    <span className="decision-weight">{(p.weight * 100).toFixed(1)}%</span>
                  </div>
                  <p className="decision-detail">
                    TrendScore {p.trend_score > 0 ? '+' : ''}{p.trend_score.toFixed(4)} (순위 {rank}위), 
                    목표 ${p.target_notional.toLocaleString()}
                    {signal && `, Vol ${(signal.realized_vol * 100).toFixed(1)}%`}
                  </p>
                  {signal && (
                    <p className="decision-reasoning">{signal.reasoning}</p>
                  )}
                </div>
              )
            })}
          </div>
        )}
        
        {/* Show excluded symbols */}
        {universe.excluded.length > 0 && (
          <div className="decision-excluded">
            <h3>제외된 심볼</h3>
            {universe.excluded.slice(0, 10).map((ex) => (
              <p key={ex.symbol} className="excluded-item">
                ○ {ex.symbol} — {ex.reason}
              </p>
            ))}
            {universe.excluded.length > 10 && (
              <p className="excluded-more">... 외 {universe.excluded.length - 10}개</p>
            )}
          </div>
        )}
      </section>
    </div>
  )
}
