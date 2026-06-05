import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { landingFor } from '../auth/auth'

// Guards a route. `allow` is an optional array of roles permitted on this route.
// - Not logged in        → redirect to /login
// - Logged in, wrong role → redirect to that role's landing page
export default function ProtectedRoute({ allow, children }) {
  const { session } = useAuth()

  if (!session) return <Navigate to="/login" replace />

  if (allow && !allow.includes(session.role)) {
    return <Navigate to={landingFor(session.role)} replace />
  }

  return children
}
