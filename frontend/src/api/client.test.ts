import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiTimeoutError, apiClient } from './client'

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
})
