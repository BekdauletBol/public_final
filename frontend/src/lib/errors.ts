import { ApiError } from './api'

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'network_error') return 'Cannot reach the server.'
    return error.message
  }
  return error instanceof Error ? error.message : String(error)
}
