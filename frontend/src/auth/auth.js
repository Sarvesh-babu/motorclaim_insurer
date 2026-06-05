// ClaimIntel — simple demo auth.
// Hardcoded credentials, role stored in localStorage. NOT real security —
// this is a role gate for the demo (see login_implementation.md §8).

const USERS = {
  user:        { password: 'user',        role: 'user',        label: 'Policyholder' },
  adjudicator: { password: 'adjudicator', role: 'adjudicator', label: 'Adjudicator' },
}

const KEY = 'claimintel_session'

export function login(username, password) {
  const u = USERS[(username || '').trim().toLowerCase()]
  if (u && u.password === password) {
    const session = {
      username: (username || '').trim().toLowerCase(),
      role: u.role,
      label: u.label,
    }
    localStorage.setItem(KEY, JSON.stringify(session))
    return session
  }
  return null
}

export function getSession() {
  try {
    return JSON.parse(localStorage.getItem(KEY))
  } catch {
    return null
  }
}

export function logout() {
  localStorage.removeItem(KEY)
}

// Where each role should land after login / when hitting a forbidden route.
export function landingFor(role) {
  return role === 'user' ? '/new' : '/'
}
