import { useEffect, useRef } from 'react'

const CHART_ID = 'bfat-tradingview-chart'

export function ChartTab() {
  const mountedRef = useRef(false)

  useEffect(() => {
    if (mountedRef.current) return
    mountedRef.current = true

    const existing = document.getElementById(CHART_ID)
    if (!existing) return

    const script = document.createElement('script')
    script.src = 'https://s3.tradingview.com/tv.js'
    script.async = true
    script.onload = () => {
      const tv = (window as unknown as { TradingView?: { widget: new (o: object) => unknown } }).TradingView
      if (!tv?.widget) return
      new tv.widget({
        width: '100%',
        height: 480,
        symbol: 'BINANCE:BTCUSDT.P',
        interval: '15',
        timezone: 'Etc/UTC',
        theme: 'dark',
        style: '1',
        locale: 'en',
        toolbar_bg: '#10151e',
        enable_publishing: false,
        allow_symbol_change: false,
        hide_side_toolbar: false,
        container_id: CHART_ID,
        overrides: {
          'paneProperties.background': '#10151e',
          'paneProperties.vertGridProperties.color': 'rgba(45,55,72,0.15)',
          'paneProperties.horzGridProperties.color': 'rgba(45,55,72,0.15)',
        },
      })
    }
    document.body.appendChild(script)
    return () => {
      script.remove()
      const el = document.getElementById(CHART_ID)
      if (el) el.innerHTML = ''
      mountedRef.current = false
    }
  }, [])

  return (
    <div className="card overflow-hidden">
      <div className="border-b border-[var(--border-subtle)] px-5 py-3">
        <div className="flex items-center gap-3">
          <p className="section-title">BTCUSDT Perpetual</p>
          <span className="badge bg-[var(--bg-elevated)] text-[var(--text-muted)]">15m</span>
        </div>
      </div>
      <div id={CHART_ID} className="min-h-[480px] w-full" />
    </div>
  )
}
