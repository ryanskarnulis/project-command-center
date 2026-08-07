import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, ApiTimeoutError, apiClient } from './client'

afterEach(() => {
  vi.restoreAllMocks()
})

/** A fetch that never resolves unless its signal aborts. */
function hangingFetch(): typeof fetch {
  return vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
    return new Promise<Response>((_resolve, reject) => {
      const signal = init?.signal
      if (!signal) return
      const fail = (): void =>
        reject(
          signal.reason instanceof Error
            ? signal.reason
            : new DOMException('Aborted', 'AbortError'),
        )
      if (signal.aborted) fail()
      else signal.addEventListener('abort', fail, { once: true })
    })
  }) as unknown as typeof fetch
}

/**
 * A fetch that resolves at the headers and then stalls mid-body — the wedged
 * backend / buffering proxy case. The stream is deliberately not wired to the
 * request's abort signal, so only `apiClient`'s own deadline can end the wait.
 */
function stalledBodyFetch(): typeof fetch {
  return vi.fn(
    () =>
      new Promise<Response>((resolve) => {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('{"partial":'))
            // Never closes.
          },
        })
        resolve(
          new Response(body, {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }),
  ) as unknown as typeof fetch
}

/** Resolve a fixed JSON payload with the given status. */
function jsonFetch(status: number, payload: unknown): typeof fetch {
  return vi.fn(() =>
    Promise.resolve(
      new Response(JSON.stringify(payload), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  ) as unknown as typeof fetch
}

describe('apiClient', () => {
  it('times out even when the caller supplies its own signal', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const controller = new AbortController()

    await expect(
      apiClient('/api/search', { signal: controller.signal, timeoutMs: 10 }),
    ).rejects.toBeInstanceOf(ApiTimeoutError)
  })

  it('surfaces a caller abort as an abort, not a timeout', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const controller = new AbortController()
    const promise = apiClient('/api/search', {
      signal: controller.signal,
      timeoutMs: 5_000,
    })
    controller.abort()

    await expect(promise).rejects.not.toBeInstanceOf(ApiTimeoutError)
  })

  it('times out with no caller signal', async () => {
    vi.stubGlobal('fetch', hangingFetch())

    await expect(apiClient('/api/search', { timeoutMs: 10 })).rejects.toBeInstanceOf(
      ApiTimeoutError,
    )
  })

  // #260: `fetch` resolves at the headers, so the deadline and the caller's
  // abort have to survive past that boundary and cover the body read too.
  it('times out when the body stalls after the headers arrive', async () => {
    vi.stubGlobal('fetch', stalledBodyFetch())

    await expect(apiClient('/api/search', { timeoutMs: 10 })).rejects.toBeInstanceOf(
      ApiTimeoutError,
    )
  })

  it('lets a caller abort cancel an in-flight body read', async () => {
    vi.stubGlobal('fetch', stalledBodyFetch())
    const controller = new AbortController()
    const promise = apiClient('/api/search', {
      signal: controller.signal,
      timeoutMs: 30_000,
    })
    // Let fetch resolve so the request is genuinely inside the body read.
    await new Promise((resolve) => setTimeout(resolve, 0))
    controller.abort()

    await expect(promise).rejects.not.toBeInstanceOf(ApiTimeoutError)
  })

  it('returns the parsed body', async () => {
    vi.stubGlobal('fetch', jsonFetch(200, { ok: true }))

    await expect(apiClient('/api/dashboard')).resolves.toEqual({ ok: true })
  })

  it('throws ApiError with the parsed body on a non-ok response', async () => {
    vi.stubGlobal('fetch', jsonFetch(409, { detail: 'nope' }))

    await expect(apiClient('/api/tasks/1')).rejects.toMatchObject({
      status: 409,
      body: { detail: 'nope' },
    })
    vi.stubGlobal('fetch', jsonFetch(409, { detail: 'nope' }))
    await expect(apiClient('/api/tasks/1')).rejects.toBeInstanceOf(ApiError)
  })

  it('parses a 204 as an undefined body rather than throwing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response(null, { status: 204 }))),
    )

    await expect(apiClient('/api/tasks/1')).resolves.toBeUndefined()
  })
})
