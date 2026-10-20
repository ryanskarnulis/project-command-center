import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { audioIdle, playText, stopPlayback, unlockAudio } from './tts'

// Issue #97: playText/currentPlayback are documented as never rejecting, but
// the speak() fetch used to sit outside any error handling, so a transport
// failure rejected both and left the hands-free loop wedged.

// jsdom's HTMLMediaElement.play() is unimplemented (it returns undefined and
// logs), so the shared element is a stand-in that ends the clip immediately.
// `holdClips` makes a clip play until paused instead (#366's long reply).
class FakeAudio {
  static holdClips = false
  static instance: FakeAudio | null = null
  src = ''
  paused = true
  pauses = 0
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor() {
    FakeAudio.instance = this
  }
  play(): Promise<void> {
    this.paused = false
    if (!FakeAudio.holdClips) {
      queueMicrotask(() => {
        this.paused = true
        this.onended?.()
      })
    }
    return Promise.resolve()
  }
  pause(): void {
    this.paused = true
    this.pauses++
  }
}

beforeEach(() => {
  FakeAudio.holdClips = false
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
  vi.useRealTimers()
})

describe('playText transport failures', () => {
  it('resolves when the speak fetch rejects', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    await expect(playText('hello')).resolves.toBe('no_audio')
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
    await expect(playText('hello')).resolves.toBe('no_audio')
    await expect(audioIdle()).resolves.toBeUndefined()
    // Nothing was loaded, so nothing should have been allocated or leaked.
    expect(URL.createObjectURL).not.toHaveBeenCalled()
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
  })

  it('gives up on a request that never answers, best-effort like a 503', async () => {
    // Issue #217: a backend or proxy that accepts the request and then never
    // settles it rejects nothing, so the catch never runs — without the client
    // deadline the vendored speak() hangs, currentPlayback never settles, and
    // the hands-free loop awaiting audioIdle() never reopens the mic.
    vi.useFakeTimers()
    const signals: (AbortSignal | undefined)[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init?: RequestInit) => {
        signals.push(init?.signal ?? undefined)
        return new Promise<Response>(() => {})
      }),
    )
    let settled = false
    const done = playText('hello').then(() => {
      settled = true
    })
    // Still waiting past the backend's own 60s speech timeout, so a slow but
    // living upstream reports its own failure rather than being cut off.
    await vi.advanceTimersByTimeAsync(60_000)
    expect(settled).toBe(false)
    await vi.advanceTimersByTimeAsync(30_000)
    await expect(done).resolves.toBeUndefined()
    // Nothing played, exactly as when voice answers 503, and the wait the
    // hands-free loop does is released.
    expect(URL.createObjectURL).not.toHaveBeenCalled()
    await expect(audioIdle()).resolves.toBeUndefined()
    // The request itself is cancelled, not just abandoned.
    expect(signals[0]?.aborted).toBe(true)
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
    await expect(playText('second')).resolves.toBe('no_audio')
    // The failed request never reached the element, so it must not revoke a
    // URL it does not own.
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
  })
})

describe('stopPlayback (#366)', () => {
  const clipFetch = () =>
    vi.fn(() =>
      Promise.resolve({ ok: true, blob: () => Promise.resolve(new Blob(['ogg'])) } as Response),
    )

  it('cuts off the clip that is playing and releases audioIdle', async () => {
    FakeAudio.holdClips = true
    vi.stubGlobal('fetch', clipFetch())
    const playback = playText('a long reply')
    await vi.waitFor(() => expect(FakeAudio.instance?.paused).toBe(false))
    let idle = false
    void audioIdle().then(() => {
      idle = true
    })
    await Promise.resolve()
    expect(idle).toBe(false)

    stopPlayback()
    await expect(playback).resolves.toBe('interrupted')
    await expect(audioIdle()).resolves.toBeUndefined()
    expect(FakeAudio.instance?.paused).toBe(true)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:clip')
  })

  it('cancels a clip still being fetched, so it never starts playing', async () => {
    const signals: (AbortSignal | undefined)[] = []
    let answer: (res: Response) => void = () => {}
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init?: RequestInit) => {
        signals.push(init?.signal ?? undefined)
        return new Promise<Response>((resolve) => {
          answer = resolve
        })
      }),
    )
    vi.mocked(URL.createObjectURL).mockReturnValueOnce('blob:late')
    const playback = playText('not yet fetched')
    await Promise.resolve()

    stopPlayback()
    await expect(playback).resolves.toBe('interrupted')
    await expect(audioIdle()).resolves.toBeUndefined()
    expect(signals[0]?.aborted).toBe(true)
    // A body that lands after the stop is dropped, not played or leaked.
    answer({ ok: true, blob: () => Promise.resolve(new Blob(['ogg'])) } as Response)
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:late')
    expect(FakeAudio.instance?.src).not.toBe('blob:late')
  })

  it('is a no-op with nothing playing, and leaves an unlock in progress alone', async () => {
    unlockAudio()
    const audio = FakeAudio.instance
    const pausesBefore = audio?.pauses ?? 0
    stopPlayback()
    expect(audio?.pauses ?? 0).toBe(pausesBefore)
    await expect(audioIdle()).resolves.toBeUndefined()
  })

  it('leaves the shared element ready for the next reply', async () => {
    FakeAudio.holdClips = true
    vi.stubGlobal('fetch', clipFetch())
    const first = playText('first')
    await vi.waitFor(() => expect(FakeAudio.instance?.paused).toBe(false))
    const shared = FakeAudio.instance
    stopPlayback()
    await first

    FakeAudio.holdClips = false
    await expect(playText('second')).resolves.toBe('ended')
    // Same element — the one a gesture unlocked — not a fresh one.
    expect(FakeAudio.instance).toBe(shared)
  })
})
