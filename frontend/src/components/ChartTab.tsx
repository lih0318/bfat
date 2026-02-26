import { useEffect, useRef } from 'react'

const CHART_ID = 'bfat-tradingview-chart'

/**
 * TradingView widget for BTCUSDT 15m. Dark theme, autosize.
 * No external backend logic.
 */
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
        height: 400,
        symbol: 'BINANCE:BTCUSDT.P',
        interval: '15',
        timezone: 'Etc/UTC',
        theme: 'dark',
        style: '1',
        locale: 'en',
        toolbar_bg: '#1a1f26',
        enable_publishing: false,
        allow_symbol_change: false,
        hide_side_toolbar: false,
        container_id: CHART_ID,
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
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg-card)] overflow-hidden shadow-[var(--shadow)] ring-1 ring-white/5 backdrop-blur-sm">
      <div className="border-b border-[var(--border)] px-4 py-2">
        <h3 className="text-sm font-semibold text-[var(--text-muted)]">BTCUSDT 15m</h3>
      </div>
      <div id={CHART_ID} className="min-h-[400px] w-full" />
    </div>
  )
}
