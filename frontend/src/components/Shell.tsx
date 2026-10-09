import { useMutation, useQueryClient } from '@tanstack/react-query'
import { NavLink, Outlet, useNavigate, useOutletContext } from 'react-router-dom'
import { auth, type Me } from '../lib/api'

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
  const logout = useMutation({
    mutationFn: auth.logout,
    onSettled: () => {
      queryClient.clear()
      navigate('/login', { replace: true })
    },
  })

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
      <main className="shell">
        <Outlet context={me} />
      </main>
    </div>
  )
}
