import type { ReactNode } from 'react'
import { errorMessage } from '../lib/errors'

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="state state-loading" role="status" aria-live="polite">
      <div className="skeleton" />
      <div className="skeleton skeleton-short" />
      <span className="visually-hidden">{label}</span>
    </div>
  )
}

export function EmptyState({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <div className="state state-empty">
      <p>{message}</p>
      {action}
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="state state-error" role="alert">
      <p>
        <span aria-hidden="true">⚠ </span>
        {errorMessage(error)}
      </p>
      {onRetry && (
        <button type="button" className="btn" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}
