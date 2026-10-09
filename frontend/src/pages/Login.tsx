import { useEffect, useState, type FormEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router-dom'
import { ApiError, auth } from '../lib/api'

export function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [retryIn, setRetryIn] = useState(0)

  useEffect(() => {
    if (retryIn <= 0) return
    const id = window.setTimeout(() => setRetryIn((s) => s - 1), 1000)
    return () => window.clearTimeout(id)
  }, [retryIn])

  const login = useMutation({
    mutationFn: () => auth.login(username, password),
    onSuccess: () => {
      queryClient.clear()
      const from = (location.state as { from?: string } | null)?.from
      navigate(from && from !== '/login' ? from : '/', { replace: true })
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 429) setRetryIn(error.retryAfterS ?? 60)
    },
  })

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (retryIn > 0 || login.isPending) return
    login.mutate()
  }

  let message: string | null = null
  if (retryIn > 0) message = `Too many attempts. Try again in ${retryIn} s.`
  else if (login.isError) {
    const err = login.error
    message =
      err instanceof ApiError && err.status === 401
        ? 'Invalid credentials'
        : err instanceof ApiError && err.code === 'network_error'
          ? 'Cannot reach the server.'
          : (err as Error).message
  }

  return (
    <main className="shell login">
      <form className="card form login-card" onSubmit={onSubmit} aria-describedby="login-error">
        <div>
          <h1>public.safe</h1>
          <p className="muted small">Admin console</p>
        </div>
        <label>
          Username
          <input
            name="username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            autoFocus
          />
        </label>
        <label>
          Password
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <p id="login-error" className="form-error" role="alert" aria-live="assertive">
          {message}
        </p>
        <button type="submit" className="btn btn-primary" disabled={login.isPending || retryIn > 0}>
          {login.isPending ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  )
}
