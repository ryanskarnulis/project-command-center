import { ApiError } from './client'

/** The human-readable failure line for a rejected API call.
 *
 * `ApiError.message` is only ever "API error <status>" — a number the user can
 * do nothing with. The backend's reason lives in `body.detail`, so prefer it
 * whenever it is present and a string: a refused mutation should say *why*
 * (a run is in flight, this would create a cycle, the due date is taken)
 * rather than leaving the user to guess.
 *
 * `fallback` covers only the non-`Error` throw — anything that is an `Error`
 * still reports its own message, which is how network and timeout failures
 * (`ApiTimeoutError`) keep their specific wording.
 *
 * This lives in `src/api/` rather than a feature folder because it is about
 * `ApiError`'s wire shape, not about any one feature (#230).
 */
export function apiErrorMessage(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    const detail = (e.body as { detail?: unknown } | null)?.detail
    if (typeof detail === 'string') return detail
  }
  return e instanceof Error ? e.message : fallback
}
