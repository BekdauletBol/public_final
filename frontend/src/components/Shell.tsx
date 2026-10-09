import { useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { NavLink, Outlet, useNavigate, useOutletContext } from 'react-router-dom'
import { auth, type Me } from '../lib/api'
import { dashboardSocket, useLive, type CameraStatusMessage } from '../lib/ws'

const NAV = [
  { to: '/', label: 'Sessions', end: true },
  { to: '/events', label: 'Events' },
  { to: '/security', label: 'Security' },
  { to: '/health', label: 'Health' },
]

export function Shell() {
  const me = useOutletContext<Me>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const live = useLive((s) => ({
    connected: s.connected,
    attempt: s.attempt,
    ever: s.everConnected,
  }))
  const logout = useMutation({
    mutationFn: auth.logout,
    onSettled: () => {
      dashboardSocket.stop()
      queryClient.clear()
      navigate('/login', { replace: true })
    },
  })

  useEffect(() => {
    dashboardSocket.start()
    const off = dashboardSocket.on((message) => {
      if (message.type === 'camera_status') {
        const m = message as CameraStatusMessage
        if (m.reason === 'snapshot') return
        void queryClient.invalidateQueries({ queryKey: ['session', m.session_id] })
        void queryClient.invalidateQueries({ queryKey: ['sessions'] })
        void queryClient.invalidateQueries({ queryKey: ['camera', m.camera_id] })
      }
    })
    return () => {
      off()
      dashboardSocket.stop()
    }
  }, [queryClient])

  return (
    <div className="layout">
      <header className="topbar">
        <a className="brand" href="/">
          public.safe
        </a>
        <nav aria-label="Main">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="topbar-user">
          <span className="muted">{me.username}</span>
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => logout.mutate()}
            disabled={logout.isPending}
          >
            Log out
          </button>
        </div>
      </header>
      {!live.connected && live.ever && (
        <div className="banner" role="status">
          Live updates paused, reconnecting… {live.attempt > 0 ? `(attempt ${live.attempt})` : ''}
        </div>
      )}
      <main className="shell">
        <Outlet context={me} />
      </main>
    </div>
  )
}
