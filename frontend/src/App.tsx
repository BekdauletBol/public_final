import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Navigate, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { ApiError, UNAUTHENTICATED_EVENT, auth } from './lib/api'
import { Loading } from './components/States'
import { ErrorBoundary } from './components/ErrorBoundary'
import { Shell } from './components/Shell'
import { Login } from './pages/Login'
import { Sessions } from './pages/Sessions'
import { SessionDetail } from './pages/SessionDetail'
import { Security } from './pages/Security'

function RequireAuth() {
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: ['me'], queryFn: auth.me, retry: false, staleTime: 60_000 })

  useEffect(() => {
    const onUnauthenticated = () => {
      queryClient.clear()
      navigate('/login', { replace: true, state: { from: location.pathname } })
    }
    window.addEventListener(UNAUTHENTICATED_EVENT, onUnauthenticated)
    return () => window.removeEventListener(UNAUTHENTICATED_EVENT, onUnauthenticated)
  }, [navigate, queryClient, location.pathname])

  if (me.isPending) return <Loading label="Checking login…" />
  if (me.isError) {
    if (me.error instanceof ApiError && me.error.status === 401) {
      return <Navigate to="/login" replace state={{ from: location.pathname }} />
    }
    return (
      <main className="shell">
        <p role="alert">Cannot reach the server. Is it running?</p>
      </main>
    )
  }
  return <Outlet context={me.data} />
}

function Placeholder({ title, milestone }: { title: string; milestone: string }) {
  return (
    <section>
      <h1>{title}</h1>
      <p className="muted">This page arrives in milestone {milestone}.</p>
    </section>
  )
}

function NotFound() {
  return (
    <section>
      <h1>Not found</h1>
      <p className="muted">There is nothing at this address.</p>
    </section>
  )
}

export default function App() {
  return (
    <ErrorBoundary>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/pair" element={<Placeholder title="Pair this phone" milestone="M3" />} />
        <Route path="/camera" element={<Placeholder title="Camera" milestone="M3" />} />
        <Route element={<RequireAuth />}>
          <Route element={<Shell />}>
            <Route index element={<Sessions />} />
            <Route path="/sessions/:id" element={<SessionDetail />} />
            <Route path="/cameras/:id" element={<Placeholder title="Camera" milestone="M3" />} />
            <Route path="/events" element={<Placeholder title="Events" milestone="M4" />} />
            <Route path="/security" element={<Security />} />
            <Route path="/health" element={<Placeholder title="Health" milestone="M3" />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Route>
      </Routes>
    </ErrorBoundary>
  )
}
