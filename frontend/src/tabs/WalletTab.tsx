import { useEffect, useState } from 'react'
import { api } from '../api/client'
import './WalletTab.css'

export function WalletTab() {
  const [balance, setBalance] = useState<Array<Record<string, unknown>> | null>(null)
  const [account, setAccount] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState<string | null>(null)

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
    </div>
  )
}
