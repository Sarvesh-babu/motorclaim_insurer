import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom'
import ErrorBoundary from './components/ErrorBoundary'
import ProtectedRoute from './components/ProtectedRoute'
import { AuthProvider, useAuth } from './auth/AuthContext'
import { landingFor } from './auth/auth'
import Login from './pages/Login'
import ClaimsQueue from './pages/ClaimsQueue'
import NewClaim from './pages/NewClaim'
import ClaimSubmitted from './pages/ClaimSubmitted'
import Dashboard from './pages/Dashboard'
import Analytics from './pages/Analytics'
import NotFound from './pages/NotFound'

function Nav() {
  const { session, logout } = useAuth()
  if (!session) return null

  const base = 'px-4 py-2 rounded-md text-sm font-medium transition-colors'
  const active = 'bg-indigo-600 text-white'
  const inactive = 'text-slate-400 hover:text-white hover:bg-slate-700'
  const cls = ({ isActive }) => `${base} ${isActive ? active : inactive}`

  const isAdjudicator = session.role === 'adjudicator'

  return (
    <nav className="flex items-center gap-2 px-6 py-4 border-b border-slate-700 bg-slate-900">
      <NavLink to={landingFor(session.role)} className="text-indigo-400 font-bold text-lg mr-6">
        ClaimIntel
      </NavLink>

      {isAdjudicator ? (
        <>
          <NavLink to="/" end className={cls}>Claims Queue</NavLink>
          <NavLink to="/new" className={cls}>New Claim</NavLink>
          <NavLink to="/analytics" className={cls}>Analytics</NavLink>
        </>
      ) : (
        <NavLink to="/new" className={cls}>File a Claim</NavLink>
      )}

      <div className="ml-auto flex items-center gap-3">
        <span className="text-xs px-2.5 py-1 rounded-full bg-slate-800 border border-slate-700 text-slate-300">
          {session.role === 'adjudicator' ? '🛡' : '👤'} {session.label}
        </span>
        <button
          onClick={logout}
          className="text-sm px-3 py-1.5 rounded-md text-slate-400 hover:text-white hover:bg-slate-700 transition-colors"
        >
          Logout
        </button>
      </div>
    </nav>
  )
}

function AppShell() {
  return (
    <div className="min-h-screen bg-slate-950">
      <Nav />
      <main className="p-6">
        <Routes>
          <Route path="/login" element={<Login />} />

          {/* User + adjudicator can file a claim */}
          <Route path="/new" element={<ProtectedRoute><NewClaim /></ProtectedRoute>} />
          <Route path="/submitted/:claimId" element={<ProtectedRoute><ClaimSubmitted /></ProtectedRoute>} />

          {/* Adjudicator-only */}
          <Route path="/" element={<ProtectedRoute allow={['adjudicator']}><ClaimsQueue /></ProtectedRoute>} />
          <Route path="/claims/:claimId" element={<ProtectedRoute allow={['adjudicator']}><Dashboard /></ProtectedRoute>} />
          <Route path="/analytics" element={<ProtectedRoute allow={['adjudicator']}><Analytics /></ProtectedRoute>} />

          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AuthProvider>
          <AppShell />
        </AuthProvider>
      </BrowserRouter>
    </ErrorBoundary>
  )
}
