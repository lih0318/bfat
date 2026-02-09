import { useEffect, useRef, useState } from 'react'
import { createChart, ColorType, LineSeries } from 'lightweight-charts'
import { api } from '../api/client'
import './WalletTab.css'

type BalanceRange = '1d' | '1w'

export function WalletTab() {
  const [balance, setBalance] = useState<Array<Record<string, unknown>> | null>(null)
  const [account, setAccount] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [balanceRange, setBalanceRange] = useState<BalanceRange>('1w')
  const [historyPoints, setHistoryPoints] = useState<Array<{ ts_epoch: number; balance: number }>>([])
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ReturnType<typeof createChart> | null>(null)
  const lineSeriesRef = useRef<{ setData: (data: { time: number; value: number }[]) => void } | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        setError(null)
        const [b, a] = await Promise.all([
          api.account.balance(),
          api.account.account(),
        ])
        if (!cancelled) {
          setBalance(b)
          setAccount(a)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }
    load()
    const t = setInterval(load, 20000)
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    api.account
      .balanceHistory(balanceRange)
      .then((res) => {
        if (!cancelled && res.points?.length) setHistoryPoints(res.points.map((p) => ({ ts_epoch: p.ts_epoch, balance: p.balance })))
        else if (!cancelled) setHistoryPoints([])
      })
      .catch(() => {
        if (!cancelled) setHistoryPoints([])
      })
    return () => { cancelled = true }
  }, [balanceRange])

  useEffect(() => {
    const container = chartContainerRef.current
    if (!container) return
    const w = container.clientWidth || 400
    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: 'var(--bg-card)' },
        textColor: 'var(--text-primary)',
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.06)' },
        horzLines: { color: 'rgba(255,255,255,0.06)' },
      },
      width: w,
      height: 260,
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: 'var(--border)',
      },
      rightPriceScale: {
        borderColor: 'var(--border)',
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      crosshair: {
        vertLine: { labelVisible: true },
        horzLine: { labelVisible: true },
      },
    })
    const lineSeries = chart.addSeries(LineSeries, {
      color: 'var(--accent)',
      lineWidth: 2,
    })
    chartRef.current = chart
    lineSeriesRef.current = lineSeries as typeof lineSeriesRef.current
    const handleResize = () => {
      if (chartRef.current && chartContainerRef.current) chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth })
    }
    window.addEventListener('resize', handleResize)
    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
      chartRef.current = null
      lineSeriesRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!lineSeriesRef.current || !historyPoints.length) return
    const data = historyPoints.map((p) => ({ time: p.ts_epoch, value: p.balance }))
    lineSeriesRef.current.setData(data)
  }, [historyPoints])

  if (error) {
    return (
      <div className="wallet-tab">
        <p className="wallet-error">{error}</p>
      </div>
    )
  }

  const usdt = Array.isArray(balance)
    ? balance.find((r) => String(r.asset || '').toUpperCase() === 'USDT')
    : null
  const walletBalance = usdt != null ? Number(usdt.balance ?? 0) : 0
  const availableBalance = usdt != null ? Number(usdt.availableBalance ?? 0) : 0
  const totalUnrealizedProfit = account != null ? Number(account.totalUnrealizedProfit ?? 0) : 0
  const totalMarginBalance = account != null ? Number(account.totalMarginBalance ?? 0) : 0

  return (
    <div className="wallet-tab">
      <h2 className="wallet-heading">Futures Wallet</h2>

      <section className="wallet-section wallet-section--live">
        <h3 className="wallet-section-title">Live (real account)</h3>
        <div className="wallet-cards">
          <div className="wallet-card">
            <span className="wallet-label">Wallet Balance (USDT)</span>
            <span className="wallet-value">{walletBalance.toFixed(2)}</span>
          </div>
          <div className="wallet-card">
            <span className="wallet-label">Available Balance</span>
            <span className="wallet-value">{availableBalance.toFixed(2)}</span>
          </div>
          <div className="wallet-card">
            <span className="wallet-label">Total Unrealized PnL</span>
            <span className={`wallet-value ${totalUnrealizedProfit >= 0 ? 'positive' : 'negative'}`}>
              {totalUnrealizedProfit >= 0 ? '+' : ''}{totalUnrealizedProfit.toFixed(2)}
            </span>
          </div>
          <div className="wallet-card">
            <span className="wallet-label">Total Margin Balance</span>
            <span className="wallet-value">{totalMarginBalance.toFixed(2)}</span>
          </div>
        </div>
      </section>

      <section className="wallet-section wallet-section--chart">
        <h3 className="wallet-section-title">자산 추이 (Total Margin Balance)</h3>
        <div className="wallet-chart-actions">
          {(['1w', '1d'] as const).map((r) => (
            <button
              key={r}
              type="button"
              className={`wallet-range-btn ${balanceRange === r ? 'active' : ''}`}
              onClick={() => setBalanceRange(r)}
            >
              {r === '1d' ? '1 Day' : '1 Week'}
            </button>
          ))}
        </div>
        <div className="wallet-chart-wrap" ref={chartContainerRef} />
        <p className="wallet-chart-note">최대 30일치 데이터 저장 · 1시간 간격 기록</p>
      </section>
    </div>
  )
}
