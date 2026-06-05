import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { landingFor } from '../auth/auth'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  const submit = (e) => {
    e.preventDefault()
    setError('')
    const s = login(username, password)
    if (!s) {
      setError('Invalid username or password')
      return
    }
    navigate(landingFor(s.role), { replace: true })
  }

  const quickFill = (u) => {
    setUsername(u)
    setPassword(u)
    setError('')
  }

  const input =
    'w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2.5 text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500'

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-950 px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-indigo-400">ClaimIntel</h1>
          <p className="text-slate-500 text-sm mt-1">Motor Insurance Claim Triage</p>
        </div>

        <form onSubmit={submit} className="bg-slate-900 rounded-2xl border border-slate-700 p-6 space-y-4">
          <h2 className="text-lg font-semibold text-white">Sign in</h2>

          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1">Username</label>
            <input
              className={input}
              placeholder="user or adjudicator"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1">Password</label>
            <input
              type="password"
              className={input}
              placeholder="••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          {error && <p className="text-red-400 text-sm">{error}</p>}

          <button
            type="submit"
            className="w-full py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold rounded-xl transition-colors"
          >
            Sign in
          </button>

          <div className="border-t border-slate-700 pt-4">
            <p className="text-xs text-slate-500 mb-2">Demo accounts — click to fill:</p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => quickFill('user')}
                className="flex-1 text-xs px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700"
              >
                👤 Policyholder
                <span className="block text-[10px] text-slate-500">user / user</span>
              </button>
              <button
                type="button"
                onClick={() => quickFill('adjudicator')}
                className="flex-1 text-xs px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700"
              >
                🛡 Adjudicator
                <span className="block text-[10px] text-slate-500">adjudicator / adjudicator</span>
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
