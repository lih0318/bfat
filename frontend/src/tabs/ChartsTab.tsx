import { useEffect, useRef, useState } from 'react'
import {
  createChart,
  ColorType,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
} from 'lightweight-charts'
import { api } from '../api/client'
import './ChartsTab.css'

type CandleItem = {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

const INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d']
const SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT']

function ema(data: number[], period: number): number[] {
  const out: number[] = []
  const k = 2 / (period + 1)
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      out.push(NaN)
    } else if (i === period - 1) {
      let sum = 0
      for (let j = 0; j < period; j++) sum += data[i - j]
      out.push(sum / period)
    } else {
      out.push(data[i] * k + out[i - 1] * (1 - k))
    }
  }
  return out
}

function sma(data: number[], period: number): number[] {
  const out: number[] = []
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      out.push(NaN)
    } else {
      let sum = 0
      for (let j = 0; j < period; j++) sum += data[i - j]
      out.push(sum / period)
    }
  }
  return out
}

function std(data: number[], period: number, meanIdx: number): number {
  let sum = 0
  for (let j = 0; j < period; j++) sum += data[meanIdx - j]
  const avg = sum / period
  let varSum = 0
  for (let j = 0; j < period; j++) varSum += (data[meanIdx - j] - avg) ** 2
  return Math.sqrt(varSum / period)
}

function bollingerBands(
  data: number[],
  period: number,
  mult: number
): { upper: number[]; middle: number[]; lower: number[] } {
  const middle = sma(data, period)
  const upper: number[] = []
  const lower: number[] = []
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      upper.push(NaN)
      lower.push(NaN)
    } else {
      const s = std(data, period, i)
      upper.push(middle[i]! + mult * s)
      lower.push(middle[i]! - mult * s)
    }
  }
  return { upper, middle, lower }
}

function formatTime(t: number): string {
  const d = new Date(t * 1000)
  return d.toLocaleString(undefined, {
    dateStyle: 'short',
    timeStyle: 'medium',
  })
}

export function ChartsTab() {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ReturnType<typeof createChart> | null>(null)
  const candleSeriesRef = useRef<{ setData: (data: CandleItem[]) => void } | null>(null)
  const volumeSeriesRef = useRef<{ setData: (data: { time: number; value: number; color?: string }[]) => void } | null>(null)
  const ema9Ref = useRef<{ setData: (data: { time: number; value: number }[]) => void } | null>(null)
  const ema21Ref = useRef<{ setData: (data: { time: number; value: number }[]) => void } | null>(null)
  const bbUpperRef = useRef<{ setData: (data: { time: number; value: number }[]) => void } | null>(null)
  const bbMiddleRef = useRef<{ setData: (data: { time: number; value: number }[]) => void } | null>(null)
  const bbLowerRef = useRef<{ setData: (data: { time: number; value: number }[]) => void } | null>(null)
  const candlesDataRef = useRef<CandleItem[]>([])
  const [symbol, setSymbol] = useState('BTCUSDT')
  const [interval, setInterval] = useState('15m')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const container = chartContainerRef.current
    const tooltip = tooltipRef.current
    if (!container || !tooltip) return
    const w = container.clientWidth || container.parentElement?.clientWidth || 800
    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: '#131920' },
        textColor: '#e8eaed',
      },
      grid: {
        vertLines: { color: 'rgba(48, 54, 61, 0.6)' },
        horzLines: { color: 'rgba(48, 54, 61, 0.6)' },
      },
      width: w,
      height: 420,
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: 'rgba(212, 168, 83, 0.2)',
      },
      rightPriceScale: {
        borderColor: 'rgba(212, 168, 83, 0.2)',
        scaleMargins: { top: 0.1, bottom: 0.25 },
      },
      crosshair: {
        vertLine: { labelVisible: true },
        horzLine: { labelVisible: true },
      },
    })

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    })
    chartRef.current = chart
    candleSeriesRef.current = candleSeries as unknown as typeof candleSeriesRef.current

    const volumePane = chart.addPane(true)
    volumePane.setStretchFactor(0.2)
    const volumeSeries = volumePane.addSeries(HistogramSeries)
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
      borderVisible: false,
    })
    volumeSeriesRef.current = volumeSeries as unknown as typeof volumeSeriesRef.current

    const ema9Series = chart.addSeries(LineSeries, {
      color: '#f59e0b',
      lineWidth: 2,
      priceScaleId: 'right',
    })
    const ema21Series = chart.addSeries(LineSeries, {
      color: '#8b5cf6',
      lineWidth: 2,
      priceScaleId: 'right',
    })
    const bbUpperSeries = chart.addSeries(LineSeries, {
      color: 'rgba(59, 130, 246, 0.8)',
      lineWidth: 1,
      priceScaleId: 'right',
    })
    const bbMiddleSeries = chart.addSeries(LineSeries, {
      color: 'rgba(59, 130, 246, 0.5)',
      lineWidth: 1,
      priceScaleId: 'right',
    })
    const bbLowerSeries = chart.addSeries(LineSeries, {
      color: 'rgba(59, 130, 246, 0.8)',
      lineWidth: 1,
      priceScaleId: 'right',
    })
    ema9Ref.current = ema9Series as unknown as typeof ema9Ref.current
    ema21Ref.current = ema21Series as unknown as typeof ema21Ref.current
    bbUpperRef.current = bbUpperSeries as unknown as typeof bbUpperRef.current
    bbMiddleRef.current = bbMiddleSeries as unknown as typeof bbMiddleRef.current
    bbLowerRef.current = bbLowerSeries as unknown as typeof bbLowerRef.current

    chart.subscribeCrosshairMove((param) => {
      if (!param.point || param.time === undefined || param.point.x < 0 || param.point.y < 0) {
        tooltip.style.display = 'none'
        return
      }
      const candleData = param.seriesData.get(candleSeries) as { open?: number; high?: number; low?: number; close?: number } | undefined
      const time = param.time as number
      const candles = candlesDataRef.current
      const candle = candles.find((c) => c.time === time)
      const vol = candle?.volume
      const o = candleData?.open ?? candle?.open
      const h = candleData?.high ?? candle?.high
      const l = candleData?.low ?? candle?.low
      const c = candleData?.close ?? candle?.close
      tooltip.style.display = 'block'
      tooltip.innerHTML = [
        `<div class="chart-tooltip-time">${formatTime(time)}</div>`,
        o != null && `<div>O ${o.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 6 })}</div>`,
        h != null && `<div>H ${h.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 6 })}</div>`,
        l != null && `<div>L ${l.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 6 })}</div>`,
        c != null && `<div>C ${c.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 6 })}</div>`,
        vol != null && `<div class="chart-tooltip-vol">Vol ${vol.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>`,
      ]
        .filter(Boolean)
        .join('')
      const rect = container.getBoundingClientRect()
      let left = param.point.x + 12
      let top = param.point.y + 12
      if (left + 180 > rect.width) left = param.point.x - 192
      if (top + 120 > rect.height) top = param.point.y - 122
      tooltip.style.left = `${left}px`
      tooltip.style.top = `${top}px`
    })

    const applyWidth = () => {
      if (chartContainerRef.current && chartRef.current) {
        const width = chartContainerRef.current.clientWidth || 800
        chartRef.current.applyOptions({ width })
      }
    }
    const ro = new ResizeObserver(applyWidth)
    ro.observe(container)
    window.addEventListener('resize', applyWidth)
    applyWidth()
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', applyWidth)
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      volumeSeriesRef.current = null
      ema9Ref.current = null
      ema21Ref.current = null
      bbUpperRef.current = null
      bbMiddleRef.current = null
      bbLowerRef.current = null
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api.klines({ symbol, interval, limit: 500 })
      .then((raw) => {
        if (cancelled || !candleSeriesRef.current || !volumeSeriesRef.current || !chartRef.current) return
        const data: CandleItem[] = raw.map((k) => ({
          time: k.time,
          open: k.open,
          high: k.high,
          low: k.low,
          close: k.close,
          volume: k.volume ?? 0,
        }))
        candlesDataRef.current = data
        candleSeriesRef.current.setData(data)

        const volumeBars = data.map((c) => ({
          time: c.time,
          value: c.volume,
          color: c.close >= c.open ? 'rgba(34, 197, 94, 0.5)' : 'rgba(239, 68, 68, 0.5)',
        }))
        volumeSeriesRef.current.setData(volumeBars)

        const closes = data.map((d) => d.close)
        const ema9Arr = ema(closes, 9)
        const ema21Arr = ema(closes, 21)
        const bb = bollingerBands(closes, 20, 2)
        const ema9Data = data.map((d, i) => ({ time: d.time, value: ema9Arr[i]! })).filter((x) => !Number.isNaN(x.value))
        const ema21Data = data.map((d, i) => ({ time: d.time, value: ema21Arr[i]! })).filter((x) => !Number.isNaN(x.value))
        const bbUpperData = data.map((d, i) => ({ time: d.time, value: bb.upper[i]! })).filter((x) => !Number.isNaN(x.value))
        const bbMiddleData = data.map((d, i) => ({ time: d.time, value: bb.middle[i]! })).filter((x) => !Number.isNaN(x.value))
        const bbLowerData = data.map((d, i) => ({ time: d.time, value: bb.lower[i]! })).filter((x) => !Number.isNaN(x.value))

        ema9Ref.current?.setData(ema9Data)
        ema21Ref.current?.setData(ema21Data)
        bbUpperRef.current?.setData(bbUpperData)
        bbMiddleRef.current?.setData(bbMiddleData)
        bbLowerRef.current?.setData(bbLowerData)
        chartRef.current?.timeScale().fitContent()
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [symbol, interval])


  return (
    <div className="charts-tab">
      <div className="charts-controls">
        <label>
          Symbol
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {SYMBOLS.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <label>
          Interval
          <select value={interval} onChange={(e) => setInterval(e.target.value)}>
            {INTERVALS.map((i) => (
              <option key={i} value={i}>{i}</option>
            ))}
          </select>
        </label>
        <span className="charts-legend">
          <span style={{ color: '#f59e0b' }}>EMA 9</span>
          <span style={{ color: '#8b5cf6' }}>EMA 21</span>
          <span style={{ color: 'rgba(59,130,246,0.8)' }}>BB</span>
        </span>
      </div>
      {error && <p className="charts-error">{error}</p>}
      {loading && <p className="charts-loading">Loading...</p>}
      <div ref={chartContainerRef} className="charts-container">
        <div ref={tooltipRef} className="chart-tooltip" />
      </div>
    </div>
  )
}
