import { apiErrorMessage } from '../../api/errorMessage'
import { ApiError } from '../../api/client'

/** Human-readable failure line for an agent run (`postMessage`).
 *
 * Shared by the agent panel and the command bar's inline ask — both post to
 * the same rate-limited endpoint, so both need the 429 wording. That copy is
 * the only agent-specific part; everything else defers to `apiErrorMessage`.
 *
 * It lives in its own module rather than in `useConversation` so the command
 * bar can import it without reaching into another feature's hook (#230).
 */
export function sendErrorMessage(e: unknown): string {
  // The rate limiter's own detail is generic; this says what to actually do.
  if (e instanceof ApiError && e.status === 429) {
    return 'Rate limited — give the agent a moment before sending more.'
  }
  return apiErrorMessage(e, 'The agent run failed')
}
