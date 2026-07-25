import { afterEach, describe, expect, it, vi } from 'vitest'
import { transcribe } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('transcribe', () => {
  it('returns the recognized text on success', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ text: 'hello' }), { status: 200 })),
    )
    await expect(transcribe(new Blob(['x']))).resolves.toBe('hello')
  })

  it('returns null on a non-2xx response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 503 })))
    await expect(transcribe(new Blob(['x']))).resolves.toBeNull()
  })

  it('returns null when fetch rejects (offline backend, DNS/proxy loss, abort)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    await expect(transcribe(new Blob(['x']))).resolves.toBeNull()
  })

  it('returns null when the body is not the expected JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>proxy error', { status: 200 })))
    await expect(transcribe(new Blob(['x']))).resolves.toBeNull()
  })

  it('returns null when the JSON lacks a string text field', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: 'nope' }), { status: 200 })),
    )
    await expect(transcribe(new Blob(['x']))).resolves.toBeNull()
  })
})
