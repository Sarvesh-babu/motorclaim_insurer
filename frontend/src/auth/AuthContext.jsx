import { createContext, useContext, useState, useCallback } from 'react'
import * as auth from './auth'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [session, setSession] = useState(() => auth.getSession())

  const login = useCallback((username, password) => {
    const s = auth.login(username, password)
    if (s) setSession(s)
    return s
  }, [])

  const logout = useCallback(() => {
    auth.logout()
    setSession(null)
  }, [])

  return (
    <AuthContext.Provider value={{ session, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
