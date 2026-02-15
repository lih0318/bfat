import { useEffect, useRef, useState } from 'react'
import { api, type MarketRegimeResponse, type RegimeTf, type PortfolioItem, type SignalItem } from '../api/client'
import './AutopilotTab.css'

/* ── Tooltip helper ────────────────────────────────────────────── */

function Tip({ text }: { text: string }) {
  return <span className="config-tip" data-tip={text}>?</span>
}

/* ── Regime Block (backward compat) ────────────────────────────── */

function RegimeBlock({ tf }: { tf: RegimeTf }) {
  const ts = (tf as any).trend_score
  return (
    <>
      <p className={`regime-value regime--${tf.regime}`}>
        {tf.regime === 'ranging' ? 'Ranging (횡보)' : 'Trending (추세)'}
        {tf.adx != null && <span className="regime-adx"> ADX: {tf.adx}</span>}
        {ts != null && <span className="regime-adx"> TS: {ts}</span>}
      </p>
      {tf.regime === 'trending' && tf.trend_direction !== 'neutral' && (
        <p className="regime-direction">
          {tf.trend_direction === 'up' ? '↑ 상승 추세' : '↓ 하락 추세'}
        </p>
      )}
      <p className="regime-hint">
        {tf.regime === 'ranging'
          ? `${tf.timeframe.toUpperCase()} — Range 전략 고려.`
          : `${tf.timeframe.toUpperCase()} — Trend 전략 고려.`}
      </p>
    </>
  )
}

/* ── Types ─────────────────────────────────────────────────────── */

interface EngineStatus {
  running: boolean
  reason: string
  profile: string
  symbol: string
  active_symbols: string[]
  equity: number
  peak_equity: number
  gross_exposure: number
  universe_size: number
}

interface ActivityItem {
  ts: string
  type: string
  symbol: string
  message: string
}

/* ── Main Component ────────────────────────────────────────────── */

export function AutopilotTab() {
  const [status, setStatus] = useState<EngineStatus | null>(null)
  const [activity, setActivity] = useState<ActivityItem[]>([])
  const [portfolio, setPortfolio] = useState<PortfolioItem[]>([])
  const [marketRegime, setMarketRegime] = useState<MarketRegimeResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [activityMode, setActivityMode] = useState<'all' | 'live'>('all')
  const formDirtyRef = useRef(false)

  // New engine config form
  const [form, setForm] = useState({
    profile: 'balanced' as string,
    signal_tf: '1d' as string,
    deadzone_threshold: 0.10,
    vol_window: 60,
    target_portfolio_vol: 0.18,
    effective_leverage_target: 5.0,
    stop_k: 2.0,
    execution_tick_sec: 120,
    entry_order_mode: 'IOC_LIMIT' as string,
    top_k_enabled: true,
    top_k: 5,
    min_weight_floor: 0.02,
    max_weight_cap: 0.40,
    rsi_period: 14,
    rsi_overbought: 70,
    rsi_oversold: 30,
    funding_scale_enabled: false,
    universe_top_n: 20,
    listing_age_days: 90,
    max_spread_pct: 0.15,
    drawdown_kill_pct: 0.15,
    symbol: 'BTCUSDT',
  })

  const load = async () => {
    try {
      setError(null)
      const [s, c, a, p] = await Promise.all([
        api.autopilot.status(),
        api.autopilot.config(),
        api.autopilot.activity(100, activityMode),
        api.autopilot.portfolio().catch(() => []),
      ])
      setStatus(s as unknown as EngineStatus)
      setActivity(Array.isArray(a) ? a : [])
      setPortfolio(Array.isArray(p) ? p : [])

      if (!formDirtyRef.current) {
        setForm((prev) => applyConfigToForm(c as Record<string, unknown>, prev))
      }

      const symbol = (c && typeof c === 'object' && (c as Record<string, unknown>).symbol)
        ? String((c as Record<string, unknown>).symbol)
        : 'BTCUSDT'
      try {
        const regime = await api.autopilot.marketRegime(symbol)
        setMarketRegime(regime)
      } catch {
        setMarketRegime(null)
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
    profile: String(config.profile ?? prev.profile),
    signal_tf: String(config.signal_tf ?? prev.signal_tf),
    deadzone_threshold: Number(config.deadzone_threshold ?? prev.deadzone_threshold),
    vol_window: Number(config.vol_window ?? prev.vol_window),
    target_portfolio_vol: Number(config.target_portfolio_vol ?? prev.target_portfolio_vol),
    effective_leverage_target: Number(config.effective_leverage_target ?? prev.effective_leverage_target),
    stop_k: Number(config.stop_k ?? prev.stop_k),
    execution_tick_sec: Number(config.execution_tick_sec ?? prev.execution_tick_sec),
    entry_order_mode: String(config.entry_order_mode ?? prev.entry_order_mode),
    top_k_enabled: Boolean(config.top_k_enabled ?? prev.top_k_enabled),
    top_k: Number(config.top_k ?? prev.top_k),
    min_weight_floor: Number(config.min_weight_floor ?? prev.min_weight_floor),
    max_weight_cap: Number(config.max_weight_cap ?? prev.max_weight_cap),
    rsi_period: Number(config.rsi_period ?? prev.rsi_period),
    rsi_overbought: Number(config.rsi_overbought ?? prev.rsi_overbought),
    rsi_oversold: Number(config.rsi_oversold ?? prev.rsi_oversold),
    funding_scale_enabled: Boolean(config.funding_scale_enabled ?? prev.funding_scale_enabled),
    universe_top_n: Number(config.universe_top_n ?? prev.universe_top_n),
    listing_age_days: Number(config.listing_age_days ?? prev.listing_age_days),
    max_spread_pct: Number(config.max_spread_pct ?? prev.max_spread_pct),
    drawdown_kill_pct: Number(config.drawdown_kill_pct ?? prev.drawdown_kill_pct),
    symbol: String(config.symbol ?? prev.symbol),
  })

  const handleSaveConfig = async () => {
    setSaving(true)
    try {
      setError(null)
      const res = await api.autopilot.putConfig({ ...form })
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

  const setField = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => {
    formDirtyRef.current = true
    setForm((f) => ({ ...f, [key]: value }))
  }

  return (
    <div className="richman-tab">
      {error && <p className="richman-error">{error}</p>}

      {/* ── Hero: Status + Regime ───────────────────────────── */}
      <div className="richman-hero">
        <div className="richman-status-card">
          <h3>TSMOM Engine</h3>
          <p className={status?.running ? 'status-running' : 'status-stopped'}>
            {status?.running ? 'Running' : 'Stopped'}
            {status?.reason ? ` — ${status.reason}` : ''}
          </p>
          {status && (
            <div className="engine-stats">
              <span>Profile: <strong>{status.profile}</strong></span>
              <span>Equity: <strong>${status.equity.toLocaleString()}</strong></span>
              <span>Exposure: <strong>${status.gross_exposure.toLocaleString()}</strong></span>
              <span>Universe: <strong>{status.universe_size}</strong></span>
              <span>Active: <strong>{status.active_symbols?.length ?? 0}</strong></span>
            </div>
          )}
          <div className="richman-actions">
            <button type="button" className="btn-start" onClick={handleStart} disabled={status?.running === true}>
              Start Engine
            </button>
            <button type="button" className="btn-stop" onClick={handleStop} disabled={!status?.running}>
              Stop Engine
            </button>
          </div>
        </div>
        {marketRegime ? (
          <>
            <div className="richman-regime-card">
              <h3>Market Regime — 1D (큰 시야) · {marketRegime.symbol}</h3>
              <RegimeBlock tf={marketRegime['1d']} />
            </div>
            <div className="richman-regime-card">
              <h3>Market Regime — 1h (세부) · {marketRegime.symbol}</h3>
              <RegimeBlock tf={marketRegime['1h']} />
            </div>
          </>
        ) : (
          <div className="richman-regime-card">
            <h3>Market Regime</h3>
            <p className="regime-unknown">로딩 중…</p>
          </div>
        )}
      </div>

      {/* ── Portfolio section (NEW) ─────────────────────────── */}
      {portfolio.length > 0 && (
        <div className="engine-portfolio">
          <h3>Portfolio</h3>
          <div className="portfolio-table-wrap">
            <table className="portfolio-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Weight</th>
                  <th>Target Qty</th>
                  <th>Notional</th>
                  <th>TrendScore</th>
                  <th>RSI</th>
                  <th>Funding</th>
                </tr>
              </thead>
              <tbody>
                {portfolio.map((p) => (
                  <tr key={p.symbol} className={`portfolio-row portfolio-row--${p.side.toLowerCase()}`}>
                    <td>{p.symbol}</td>
                    <td className={p.side === 'LONG' ? 'side-long' : 'side-short'}>{p.side}</td>
                    <td>{(p.weight * 100).toFixed(1)}%</td>
                    <td>{p.target_qty.toFixed(6)}</td>
                    <td>${p.target_notional.toLocaleString()}</td>
                    <td className={p.trend_score > 0 ? 'ts-positive' : p.trend_score < 0 ? 'ts-negative' : ''}>
                      {p.trend_score.toFixed(4)}
                    </td>
                    <td>{p.rsi?.toFixed(1) ?? '—'}</td>
                    <td>{p.funding_rate != null ? (p.funding_rate * 100).toFixed(4) + '%' : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Settings ────────────────────────────────────────── */}
      <div className="richman-top">
        <div className="richman-config-card">
          <h3>Engine Settings</h3>
          <div className="config-sections">

            {/* Profile */}
            <section className="config-section">
              <h4>Profile</h4>
              <div className="config-row">
                <label>
                  <span>Profile <Tip text="프리셋 선택. Conservative=낮은 변동성/레버리지, Balanced=중간, Aggressive=높은 변동성/빠른 실행. Custom은 수동 설정." /></span>
                  <select
                    value={form.profile}
                    onChange={(e) => setField('profile', e.target.value)}
                  >
                    <option value="conservative">Conservative (보수적)</option>
                    <option value="balanced">Balanced (균형)</option>
                    <option value="aggressive">Aggressive (공격적)</option>
                    <option value="custom">Custom (사용자 정의)</option>
                  </select>
                </label>
                <label>
                  <span>Signal TF <Tip text="시그널(TrendScore) 계산에 사용할 타임프레임. 1D=일봉 기준(느리지만 안정적), 4H=4시간봉(빠른 반응)." /></span>
                  <select
                    value={form.signal_tf}
                    onChange={(e) => setField('signal_tf', e.target.value)}
                  >
                    <option value="1d">1D (일봉)</option>
                    <option value="4h">4H (4시간봉)</option>
                  </select>
                </label>
                <label>
                  <span>Default Symbol <Tip text="Market Regime 카드에 표시할 심볼. 실제 거래 심볼은 Universe에서 자동 선정되므로, 이 값은 거래에 영향을 주지 않습니다." /></span>
                  <input
                    type="text"
                    value={form.symbol}
                    onChange={(e) => setField('symbol', e.target.value.toUpperCase())}
                  />
                </label>
              </div>
            </section>

            {/* Signal */}
            <section className="config-section">
              <h4>Signal</h4>
              <div className="config-row">
                <label>
                  <span>Deadzone <Tip text="TrendScore의 절대값이 이 값보다 작으면 포지션을 잡지 않습니다 (노이즈 필터). 높을수록 보수적. 기본 0.10." /></span>
                  <input type="number" min={0} max={1} step={0.01} value={form.deadzone_threshold}
                    onChange={(e) => setField('deadzone_threshold', Number(e.target.value) || 0)} />
                </label>
                <label>
                  <span>RSI Period <Tip text="RSI 계산에 사용할 봉 수. 일반적으로 14. 작을수록 민감, 클수록 둔감." /></span>
                  <input type="number" min={5} max={50} value={form.rsi_period}
                    onChange={(e) => setField('rsi_period', Number(e.target.value) || 14)} />
                </label>
                <label>
                  <span>RSI 과매수 <Tip text="RSI가 이 값 이상이면 롱 포지션 크기를 축소합니다. 기본 70. 높을수록 기준이 느슨." /></span>
                  <input type="number" min={50} max={100} value={form.rsi_overbought}
                    onChange={(e) => setField('rsi_overbought', Number(e.target.value) || 70)} />
                </label>
                <label>
                  <span>RSI 과매도 <Tip text="RSI가 이 값 이하이면 롱 포지션 크기를 확대합니다. 기본 30. 낮을수록 기준이 느슨." /></span>
                  <input type="number" min={0} max={50} value={form.rsi_oversold}
                    onChange={(e) => setField('rsi_oversold', Number(e.target.value) || 30)} />
                </label>
              </div>
              <div className="config-row">
                <label className="config-checkbox">
                  <input type="checkbox" checked={form.funding_scale_enabled}
                    onChange={(e) => setField('funding_scale_enabled', e.target.checked)} />
                  <span>Funding Rate 오버레이 활성화 <Tip text="활성화 시, 높은 펀딩비가 포지션 방향과 반대면 크기를 축소합니다. 펀딩비 비용을 고려한 리스크 관리." /></span>
                </label>
              </div>
            </section>

            {/* Sizing */}
            <section className="config-section">
              <h4>Sizing / Risk</h4>
              <div className="config-row">
                <label>
                  <span>Target Vol (연율) <Tip text="포트폴리오 전체의 연간 목표 변동성. 예: 0.10 = 연 10%. 높을수록 공격적 사이징, 낮을수록 보수적." /></span>
                  <input type="number" min={0.01} max={1} step={0.01} value={form.target_portfolio_vol}
                    onChange={(e) => setField('target_portfolio_vol', Number(e.target.value) || 0.1)} />
                </label>
                <label>
                  <span>Leverage Target <Tip text="포트폴리오 총 노출 배수 (gross notional / equity). 예: 5 = 자본의 5배까지 노출. 바이낸스 계좌 레버리지는 마진 효율을 위해 별도로 10~20배 자동 설정됩니다." /></span>
                  <input type="number" min={0.5} max={20} step={0.5} value={form.effective_leverage_target}
                    onChange={(e) => setField('effective_leverage_target', Number(e.target.value) || 1)} />
                </label>
                <label>
                  <span>Vol Window (days) <Tip text="실현변동성(realized vol) 계산에 사용할 일수. 길수록 안정적이지만 최근 변화 반영이 느림. 기본 60일." /></span>
                  <input type="number" min={10} max={365} value={form.vol_window}
                    onChange={(e) => setField('vol_window', Number(e.target.value) || 60)} />
                </label>
                <label>
                  <span>Stop K (ATR x) <Tip text="손절가 거리 = K × ATR. 클수록 넓은 손절(변동 허용), 작을수록 타이트한 손절. 기본 2.0." /></span>
                  <input type="number" min={0.5} max={10} step={0.1} value={form.stop_k}
                    onChange={(e) => setField('stop_k', Number(e.target.value) || 2)} />
                </label>
              </div>
              <div className="config-row">
                <label>
                  <span>Drawdown Kill (%) <Tip text="고점 대비 이 비율 이상 하락하면 엔진이 자동 정지됩니다. 예: 10 = 고점 대비 10% 하락 시 정지. 자본 보호 장치." /></span>
                  <input type="number" min={1} max={100} step={1}
                    value={Math.round(form.drawdown_kill_pct * 100)}
                    onChange={(e) => setField('drawdown_kill_pct', (Number(e.target.value) || 10) / 100)} />
                </label>
              </div>
            </section>

            {/* Top-K */}
            <section className="config-section">
              <h4>Top-K Concentration</h4>
              <div className="config-row">
                <label className="config-checkbox">
                  <input type="checkbox" checked={form.top_k_enabled}
                    onChange={(e) => setField('top_k_enabled', e.target.checked)} />
                  <span>Top-K 활성화 <Tip text="활성화 시, TrendScore가 가장 강한 상위 K개 심볼에만 집중 투자. 비활성화 시 전체 유니버스에 분산." /></span>
                </label>
                <label>
                  <span>K (최대 포지션 수) <Tip text="동시에 보유할 최대 포지션 수. 적을수록 집중 투자, 많을수록 분산. 기본 5." /></span>
                  <input type="number" min={1} max={50} value={form.top_k}
                    onChange={(e) => setField('top_k', Number(e.target.value) || 5)} />
                </label>
                <label>
                  <span>Min Weight (%) <Tip text="심볼당 최소 비중. 이 비율 미만이면 해당 심볼 포지션을 잡지 않습니다 (너무 작은 포지션 방지). 기본 2%." /></span>
                  <input type="number" min={0} max={50} step={1}
                    value={Math.round(form.min_weight_floor * 100)}
                    onChange={(e) => setField('min_weight_floor', (Number(e.target.value) || 2) / 100)} />
                </label>
                <label>
                  <span>Max Weight (%) <Tip text="심볼당 최대 비중. 단일 심볼 집중 위험을 방지합니다. 기본 40%." /></span>
                  <input type="number" min={5} max={100} step={1}
                    value={Math.round(form.max_weight_cap * 100)}
                    onChange={(e) => setField('max_weight_cap', (Number(e.target.value) || 40) / 100)} />
                </label>
              </div>
            </section>

            {/* Execution */}
            <section className="config-section">
              <h4>Execution</h4>
              <div className="config-row">
                <label>
                  <span>Order Mode <Tip text="주문 방식. IOC Limit=즉시체결 지정가(미체결분 취소), Post-Only=메이커 전용(수수료 절약), Market=즉시 시장가(빠른 체결)." /></span>
                  <select value={form.entry_order_mode}
                    onChange={(e) => setField('entry_order_mode', e.target.value)}>
                    <option value="IOC_LIMIT">IOC Limit</option>
                    <option value="POST_ONLY_LIMIT">Post-Only Limit</option>
                    <option value="MARKET">Market</option>
                  </select>
                </label>
                <label>
                  <span>Exec Tick (초) <Tip text="실행 틱 간격(초). 이 주기마다 현재 포지션과 목표를 비교하여 주문을 실행. 짧을수록 빠른 반응이지만 API 사용량 증가. 기본 120초." /></span>
                  <input type="number" min={60} max={300} value={form.execution_tick_sec}
                    onChange={(e) => setField('execution_tick_sec', Number(e.target.value) || 120)} />
                </label>
              </div>
            </section>

            {/* Universe */}
            <section className="config-section">
              <h4>Universe</h4>
              <div className="config-row">
                <label>
                  <span>Top N (24h Volume) <Tip text="24시간 거래량 기준 상위 N개 심볼만 거래 대상으로 선정. 높을수록 많은 심볼 포함. 기본 20." /></span>
                  <input type="number" min={1} max={200} value={form.universe_top_n}
                    onChange={(e) => setField('universe_top_n', Number(e.target.value) || 20)} />
                </label>
                <label>
                  <span>Min Listing Age (days) <Tip text="바이낸스 상장 후 최소 경과 일수. 신규 상장 코인의 불안정성을 피하기 위한 필터. 기본 90일." /></span>
                  <input type="number" min={0} max={3650} value={form.listing_age_days}
                    onChange={(e) => setField('listing_age_days', Number(e.target.value) || 90)} />
                </label>
                <label>
                  <span>Max Spread (%) <Tip text="매수/매도 호가 스프레드 허용 최대치(%). 이 이상이면 유동성 부족으로 거래 대상에서 제외. 기본 0.15%." /></span>
                  <input type="number" min={0} max={5} step={0.01} value={form.max_spread_pct}
                    onChange={(e) => setField('max_spread_pct', Number(e.target.value) || 0.15)} />
                </label>
              </div>
            </section>
          </div>

          <div className="config-footer">
            <button type="button" className="btn-save" onClick={handleSaveConfig} disabled={saving}>
              {saving ? 'Saving...' : 'Save Config'}
            </button>
          </div>
        </div>
      </div>

      {/* ── Activity Log ───────────────────────────────────── */}
      <div className="richman-activity">
        <h3>Activity Log</h3>
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
