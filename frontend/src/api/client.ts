export class ApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(status: number, body: unknown) {
    super(`API error ${status}`)
    this.status = status
    this.body = body
  }
}

export class ApiTimeoutError extends Error {
  constructor(timeoutMs: number) {
    super(`The request timed out after ${Math.round(timeoutMs / 1000)}s — is the backend running?`)
  }
}

const BASE_URL =
  import.meta.env.VITE_API_URL ?? `http://${window.location.hostname}:8101`

// Every request aborts eventually so a hung backend can't leave a permanent
// spinner.
const DEFAULT_TIMEOUT_MS = 30_000

export interface ApiOptions extends RequestInit {
  timeoutMs?: number
}

function abortError(signal: AbortSignal): Error {
  return signal.reason instanceof Error
    ? signal.reason
    : new DOMException('Aborted', 'AbortError')
}

/**
 * Read and parse the response body, losing the race to `signal` if it aborts
 * first. `fetch` resolves at the headers, so a backend that stalls mid-body
 * would otherwise hang here forever — racing the abort (rather than trusting
 * the body stream to notice `controller.abort()`) is what makes the deadline
 * cover the response half of the request too.
 *
 * Bodiless success statuses parse to `undefined`; the DELETE routes return 204.
 */
async function readBody(response: Response, signal: AbortSignal): Promise<unknown> {
  if (response.status === 204 || response.status === 205) return undefined
  let onAbort: () => void = () => {}
  // `Promise.race` attaches a rejection handler to `aborted`, so a late abort
  // after the body already parsed can't surface as an unhandled rejection.
  const aborted = new Promise<never>((_resolve, reject) => {
    onAbort = (): void => reject(abortError(signal))
    if (signal.aborted) onAbort()
    else signal.addEventListener('abort', onAbort, { once: true })
  })
  try {
    return await Promise.race([response.json(), aborted])
  } finally {
    signal.removeEventListener('abort', onAbort)
  }
}

/**
 * Perform a request and return its parsed JSON body.
 *
 * `apiClient` owns body parsing so the deadline and the caller's abort stay
 * live until the body is actually consumed — callers get parsed data, never a
 * half-read `Response` whose cancellation plumbing has already been torn down.
 */
export async function apiClient<T = unknown>(
  path: string,
  options?: ApiOptions,
): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...init } = options ?? {}
  // Caller cancellation and the deadline both stay live: whichever fires first
  // aborts the request. (Composed by hand rather than with `AbortSignal.any`,
  // which jsdom doesn't implement.)
  const callerSignal = init.signal ?? null
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  const onCallerAbort = (): void => {
    controller.abort(callerSignal?.reason)
  }
  if (callerSignal) {
    if (callerSignal.aborted) onCallerAbort()
    else callerSignal.addEventListener('abort', onCallerAbort, { once: true })
  }

  try {
    const response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
    })
    if (!response.ok) {
      // A missing or malformed error body still yields an ApiError; only an
      // abort or the deadline escapes to the handler below.
      let body: unknown = null
      try {
        body = await readBody(response, controller.signal)
      } catch (e: unknown) {
        if (controller.signal.aborted) throw e
      }
      throw new ApiError(response.status, body)
    }
    return (await readBody(response, controller.signal)) as T
  } catch (e: unknown) {
    // A real status beats the clock: a response we managed to classify stays an
    // ApiError even if the deadline expired on its way out.
    if (e instanceof ApiError) throw e
    // Only the deadline maps to ApiTimeoutError; a caller abort stays an abort.
    if (timedOut && !callerSignal?.aborted) {
      throw new ApiTimeoutError(timeoutMs)
    }
    throw e
  } finally {
    clearTimeout(timer)
    callerSignal?.removeEventListener('abort', onCallerAbort)
  }
}
