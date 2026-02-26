import './App.css'
import { AuthProvider, useAuth } from './context/AuthContext'
import { Dashboard } from './components/Dashboard'
import { LoginPage } from './components/LoginPage'

function AppContent() {
  const { isAuthenticated, isLoading } = useAuth()
  if (isLoading) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-[#0a0e12]">
        <div className="h-10 w-10 animate-spin-slow rounded-full border-2 border-[var(--border)] border-t-[var(--accent)]" />
        <p className="text-sm text-[var(--text-muted)]">초기화 중...</p>
      </div>
    )
  }
  return isAuthenticated ? <Dashboard /> : <LoginPage />
}

function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  )
}

export default App
