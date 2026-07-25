import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { audioIdle, playText } from './tts'

// Issue #97: playText/currentPlayback are documented as never rejecting, but
// the speak() fetch used to sit outside any error handling, so a transport
// failure rejected both and left the hands-free loop wedged.

// jsdom's HTMLMediaElement.play() is unimplemented (it returns undefined and
// logs), so the shared element is a stand-in that ends the clip immediately.
class FakeAudio {
  src = ''
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  play(): Promise<void> {
    queueMicrotask(() => this.onended?.())
    return Promise.resolve()
  }
}

beforeEach(() => {
  vi.stubGlobal('Audio', FakeAudio)
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: vi.fn(() => 'blob:clip'),
    revokeObjectURL: vi.fn(),
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('playText transport failures', () => {
  it('resolves when the speak fetch rejects', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    await expect(playText('hello')).resolves.toBeUndefined()
  })

  it('leaves audioIdle resolvable after a rejected fetch', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    await playText('hello')
    await expect(audioIdle()).resolves.toBeUndefined()
  })

  it('resolves when the response body cannot be read', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          blob: () => Promise.reject(new Error('stream aborted')),
        } as unknown as Response),
      ),
    )
    await expect(playText('hello')).resolves.toBeUndefined()
    await expect(audioIdle()).resolves.toBeUndefined()
    // Nothing was loaded, so nothing should have been allocated or leaked.
    expect(URL.createObjectURL).not.toHaveBeenCalled()
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
  })

  it('does not disturb a clip already loaded in the shared element', async () => {
    // A successful clip first: jsdom's Audio has no real playback, so play()
    // rejects and the promise settles through the same finish() path.
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({ ok: true, blob: () => Promise.resolve(new Blob(['ogg'])) } as Response),
      ),
    )
    await playText('first')
    vi.mocked(URL.revokeObjectURL).mockClear()

    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    await expect(playText('second')).resolves.toBeUndefined()
    // The failed request never reached the element, so it must not revoke a
    // URL it does not own.
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
  })
})
