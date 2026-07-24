import { describe, expect, it, vi } from 'vitest'
import { fireAndForget } from './async'

describe('fireAndForget', () => {
  it('returns undefined and does not throw for a resolving promise', () => {
    expect(fireAndForget(Promise.resolve('ok'))).toBeUndefined()
  })

  it('swallows a rejected promise without raising an unhandled rejection', async () => {
    fireAndForget(Promise.reject(new Error('boom')))
    // Flush the microtask + macrotask queues. The setup.ts backstop fails this
    // test if the helper lets the rejection escape.
    await new Promise((resolve) => setTimeout(resolve, 0))
  })

  it('runs a chained follow-up on success', async () => {
    const followUp = vi.fn()
    fireAndForget(Promise.resolve().then(followUp))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(followUp).toHaveBeenCalledOnce()
  })

  it('skips a chained follow-up when the promise rejects', async () => {
    const followUp = vi.fn()
    fireAndForget(Promise.reject(new Error('boom')).then(followUp))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(followUp).not.toHaveBeenCalled()
  })
})
