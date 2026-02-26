import './App.css'
import { AuthProvider, useAuth } from './context/AuthContext'
import { Dashboard } from './components/Dashboard'
import { LoginPage } from './components/LoginPage'

function AppContent() {
  const { isAuthenticated, isLoading } = useAuth()
  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#0a0e12]">
        <p className="text-[var(--text-muted)]">Loading...</p>
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
