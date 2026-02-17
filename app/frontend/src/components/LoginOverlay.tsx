import { useState } from 'react'

interface Props {
  onLogin: (secret: string) => void
}

export default function LoginOverlay({ onLogin }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      const data = await res.json()
      if (res.ok && data.token) {
        onLogin(data.token)
      } else {
        setError(data.message || 'Login failed')
      }
    } catch {
      setError('Cannot reach server')
    }
    setLoading(false)
  }

  return (
    <div className="login-overlay">
      <div className="login-card">
        <img src="/logo.png" alt="octoclaw" className="login-card__logo" />
        <p className="login-card__subtitle">Sign in to continue</p>
        <form onSubmit={handleSubmit} className="login-card__form">
          <input
            type="text"
            value={username}
            onChange={e => setUsername(e.target.value)}
            placeholder="Username"
            className="input"
            autoFocus
            autoComplete="username"
          />
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="Password"
            className="input"
            autoComplete="current-password"
          />
          {error && <p className="login-card__error">{error}</p>}
          <button type="submit" className="btn btn--primary" disabled={loading || !username.trim() || !password}>
            {loading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  )
}
