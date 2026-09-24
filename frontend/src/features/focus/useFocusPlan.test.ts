import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getFocusPlan } from '../../api/focus'
import type { FocusPlan } from '../../types/focus'
import { useFocusPlan } from './useFocusPlan'

vi.mock('../../api/focus', () => ({
  getFocusPlan: vi.fn(),
}))

const mockGetFocusPlan = vi.mocked(getFocusPlan)

const emptyPlan: FocusPlan = {
  date: '2026-06-20',
  start_time: '09:00',
  available_minutes: 30,
  used_minutes: 0,
  scheduled: [],
  overflow: [],
  blocked: [],
}

/** The capacity the hook sent on its most recent plan request. */
function lastRequest() {
  const calls = mockGetFocusPlan.mock.calls
  return calls[calls.length - 1][0] ?? {}
}

describe('useFocusPlan request window', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date(2026, 5, 20, 9, 0, 0))
    // A 30-minute finite session.
    localStorage.setItem('focus.capacityMode', 'minutes')
    localStorage.setItem('focus.capacity', '30')
    mockGetFocusPlan.mockResolvedValue(emptyPlan)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  async function settle(result: { current: ReturnType<typeof useFocusPlan> }) {
    await waitFor(() => expect(result.current.loading).toBe(false))
  }

  it('asks for the configured capacity when nothing is consumed (desktop never consumes)', async () => {
    const { result } = renderHook(() => useFocusPlan())
    await settle(result)

    expect(lastRequest()).toMatchObject({ startTime: '09:00', availableMinutes: 30 })
    expect(result.current.availableMinutes).toBe(30)
  })

  it.each([
    // [consumed, expected remaining window]
    [15, 15],
    [16, 14],
    [29, 1],
    [30, 0],
    [45, 0],
  ])('after %i consumed minutes of 30 it asks for exactly %i', async (consumed, remaining) => {
    const { result } = renderHook(() => useFocusPlan())
    await settle(result)

    act(() => result.current.setConsumedMinutes(consumed))
    await settle(result)

    expect(lastRequest().availableMinutes).toBe(remaining)
    // The configured capacity the page displays is untouched by the residual.
    expect(result.current.availableMinutes).toBe(30)
  })

  it('never refills an exhausted session across repeated completions', async () => {
    const { result } = renderHook(() => useFocusPlan())
    await settle(result)

    for (const consumed of [20, 30, 40, 50]) {
      act(() => result.current.setConsumedMinutes(consumed))
      await settle(result)
      expect(lastRequest().availableMinutes).toBe(Math.max(0, 30 - consumed))
    }
  })

  it('reopens the residual window once the session is explicitly extended', async () => {
    const { result } = renderHook(() => useFocusPlan())
    await settle(result)

    act(() => result.current.setConsumedMinutes(30))
    await settle(result)
    expect(lastRequest().availableMinutes).toBe(0)

    act(() => result.current.setCapacityMinutes(40))
    await settle(result)
    expect(lastRequest().availableMinutes).toBe(10)
  })
})
