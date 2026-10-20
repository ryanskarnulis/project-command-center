import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../../api/client'
import {
  clearFocusClock,
  finishFocusBlock,
  getFocusClock,
  getFocusSessionLog,
  removeFocusBlock,
  setFocusClock,
} from '../../../api/focusSessions'
import type { FocusClock } from '../../../types/focus'
import { resetFocusSessionSyncForTests, unsentWriteCount } from './sessionSync'
import { useFocusClock } from './useFocusClock'
import { useFocusSessionLog } from './useFocusSessionLog'

vi.mock('../../../api/focusSessions', () => ({
  clearFocusClock: vi.fn(),
  finishFocusBlock: vi.fn(),
  getFocusClock: vi.fn(),
  getFocusSessionLog: vi.fn(),
  removeFocusBlock: vi.fn(),
  setFocusClock: vi.fn(),
}))

const mockGetClock = vi.mocked(getFocusClock)
const mockSetClock = vi.mocked(setFocusClock)
const mockClearClock = vi.mocked(clearFocusClock)
const mockGetLog = vi.mocked(getFocusSessionLog)
const mockFinish = vi.mocked(finishFocusBlock)
const mockRemove = vi.mocked(removeFocusBlock)

const DAY = '2026-06-20'
const MINUTE = 60_000

function serverClock(overrides: Partial<FocusClock> = {}): FocusClock {
  return {
    id: 1,
    plan_date: DAY,
    task_id: 7,
    state: 'running',
    elapsed_ms: 0,
    leg_started_at: null,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  resetFocusSessionSyncForTests()
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(2026, 5, 20, 9, 0, 0))
  mockGetClock.mockResolvedValue(null)
  mockSetClock.mockImplementation(async (plan_date, task_id, state, elapsed_ms) =>
    serverClock({ plan_date, task_id, state, elapsed_ms }),
  )
  mockClearClock.mockResolvedValue(undefined)
  mockGetLog.mockImplementation(async (plan_date) => ({ plan_date, finished: [] }))
  mockFinish.mockImplementation(async (plan_date, task_id, minutes, planned_minutes) => ({
    id: 1,
    plan_date,
    task_id,
    title: 'x',
    minutes,
    planned_minutes,
    finished_at: '',
  }))
  mockRemove.mockResolvedValue(undefined)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useFocusClock', () => {
  it('picks up the clock the server holds — a reload or another device', async () => {
    mockGetClock.mockResolvedValue(serverClock({ elapsed_ms: 10 * MINUTE }))
    const { result } = renderHook(() => useFocusClock(7, DAY))

    await waitFor(() => expect(result.current.run).toBe('running'))
    expect(result.current.elapsed).toBe(10)
    // Still counting from the server's reading.
    act(() => {
      vi.advanceTimersByTime(5 * MINUTE)
    })
    expect(result.current.elapsed).toBe(15)
  })

  it('ignores a server clock that belongs to another block or day', async () => {
    mockGetClock.mockResolvedValue(serverClock({ task_id: 8, elapsed_ms: 10 * MINUTE }))
    const { result } = renderHook(() => useFocusClock(7, DAY))
    await waitFor(() => expect(mockGetClock).toHaveBeenCalled())
    expect(result.current.run).toBe('idle')
  })

  it('start, pause and reset write through to the server', async () => {
    const { result } = renderHook(() => useFocusClock(7, DAY))
    await waitFor(() => expect(mockGetClock).toHaveBeenCalled())

    act(() => result.current.start())
    expect(result.current.run).toBe('running') // optimistic
    await waitFor(() => expect(mockSetClock).toHaveBeenCalledWith(DAY, 7, 'running', 0))

    act(() => {
      vi.advanceTimersByTime(3 * MINUTE)
    })
    act(() => result.current.pause())
    expect(result.current.run).toBe('paused')
    await waitFor(() => expect(mockSetClock).toHaveBeenLastCalledWith(DAY, 7, 'paused', expect.any(Number)))
    const banked = mockSetClock.mock.calls[1][3]
    expect(banked).toBeGreaterThanOrEqual(3 * MINUTE)

    act(() => result.current.reset())
    expect(result.current.run).toBe('idle')
    await waitFor(() => expect(mockClearClock).toHaveBeenCalledWith(DAY, 7))
  })

  it('restores an undone clock on the server with its time', async () => {
    const { result } = renderHook(() => useFocusClock(7, DAY))
    await waitFor(() => expect(mockGetClock).toHaveBeenCalled())
    act(() => result.current.start())
    act(() => {
      vi.advanceTimersByTime(4 * MINUTE + 30_000) // the readout samples on a 15s tick
    })
    const snapshot = result.current.snapshot()
    act(() => result.current.reset())
    act(() => result.current.restore(snapshot))

    expect(result.current.run).toBe('running')
    expect(result.current.elapsed).toBe(4)
    await waitFor(() => expect(mockSetClock).toHaveBeenCalledTimes(2))
    expect(mockSetClock.mock.calls[1][3]).toBeGreaterThanOrEqual(4 * MINUTE)
  })

  it('re-reads the server when a write is refused', async () => {
    const { result } = renderHook(() => useFocusClock(7, DAY))
    await waitFor(() => expect(mockGetClock).toHaveBeenCalledTimes(1))
    mockSetClock.mockRejectedValueOnce(new ApiError(404, { detail: 'Task not found' }))

    act(() => result.current.start())
    await waitFor(() => expect(result.current.run).toBe('idle'))
    expect(mockGetClock).toHaveBeenCalledTimes(2)
  })
})

describe('offline: the outbox', () => {
  it('keeps a write that could not reach the server and sends it once it can', async () => {
    const { result } = renderHook(() => useFocusClock(7, DAY))
    await waitFor(() => expect(mockGetClock).toHaveBeenCalledTimes(1))
    mockSetClock.mockRejectedValueOnce(new TypeError('Failed to fetch'))

    act(() => result.current.start())
    await waitFor(() => expect(mockSetClock).toHaveBeenCalledTimes(1))
    expect(unsentWriteCount()).toBe(1)
    expect(result.current.run).toBe('running') // the screen keeps what the user did

    // Coming back to the tab delivers the write before it reads, so the read
    // sees the clock rather than the server's stale "no clock".
    mockGetClock.mockResolvedValue(serverClock({ elapsed_ms: 0 }))
    act(() => document.dispatchEvent(new Event('visibilitychange')))
    await waitFor(() => expect(mockGetClock).toHaveBeenCalledTimes(2))
    expect(mockSetClock).toHaveBeenCalledTimes(2)
    expect(mockSetClock.mock.invocationCallOrder[1]).toBeLessThan(
      mockGetClock.mock.invocationCallOrder[1],
    )
    expect(mockSetClock.mock.calls[1]).toEqual([DAY, 7, 'running', expect.any(Number)])
    expect(unsentWriteCount()).toBe(0)
    expect(result.current.run).toBe('running')
  })

  it('skips a read while writes are still unsent, then retries on a timer', async () => {
    const { result } = renderHook(() => useFocusSessionLog(DAY))
    await waitFor(() => expect(mockGetLog).toHaveBeenCalledTimes(1))
    mockFinish.mockRejectedValue(new TypeError('Failed to fetch'))

    act(() => result.current.record({ taskId: 3, title: 'Brief', minutes: 40, plannedMinutes: 30 }))
    await waitFor(() => expect(mockFinish).toHaveBeenCalledTimes(1))
    act(() => document.dispatchEvent(new Event('visibilitychange')))
    await waitFor(() => expect(mockFinish).toHaveBeenCalledTimes(2))
    expect(mockGetLog).toHaveBeenCalledTimes(1) // the stale server log was never read
    expect(result.current.spent).toBe(40)

    mockFinish.mockReset()
    mockFinish.mockResolvedValue({
      id: 1, plan_date: DAY, task_id: 3, title: 'Brief', minutes: 40, planned_minutes: 30, finished_at: '',
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(mockFinish).toHaveBeenCalledWith(DAY, 3, 40, 30)
    expect(unsentWriteCount()).toBe(0)
    expect(result.current.spent).toBe(40)
  })

  it('sends the unsent writes in the order they were made', async () => {
    const { result } = renderHook(() => useFocusClock(7, DAY))
    await waitFor(() => expect(mockGetClock).toHaveBeenCalledTimes(1))
    mockSetClock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    act(() => result.current.start())
    await waitFor(() => expect(unsentWriteCount()).toBe(1))
    act(() => result.current.pause())
    await waitFor(() => expect(unsentWriteCount()).toBe(0))
    expect(mockSetClock.mock.calls.map((call) => call[2])).toEqual(['running', 'running', 'paused'])
  })
})

describe('useFocusSessionLog', () => {
  it('loads the day’s finished blocks from the server', async () => {
    mockGetLog.mockResolvedValue({
      plan_date: DAY,
      finished: [
        { id: 1, plan_date: DAY, task_id: 3, title: 'Brief', minutes: 40, planned_minutes: 30, finished_at: '' },
      ],
    })
    const { result } = renderHook(() => useFocusSessionLog(DAY))
    await waitFor(() => expect(result.current.finished).toHaveLength(1))
    expect(result.current.finished[0]).toEqual({ taskId: 3, title: 'Brief', minutes: 40, plannedMinutes: 30 })
    expect(result.current.spent).toBe(40)
    expect(mockGetLog).toHaveBeenCalledWith(DAY)
  })

  it('records and removes optimistically, writing behind', async () => {
    const { result } = renderHook(() => useFocusSessionLog(DAY))
    await waitFor(() => expect(mockGetLog).toHaveBeenCalled())

    act(() => result.current.record({ taskId: 3, title: 'Brief', minutes: 40, plannedMinutes: 30 }))
    expect(result.current.spent).toBe(40)
    await waitFor(() => expect(mockFinish).toHaveBeenCalledWith(DAY, 3, 40, 30))

    act(() => result.current.remove(3))
    expect(result.current.finished).toEqual([])
    await waitFor(() => expect(mockRemove).toHaveBeenCalledWith(DAY, 3))
  })

  it('keeps one log per day', async () => {
    mockGetLog.mockImplementation(async (plan_date) => ({
      plan_date,
      finished:
        plan_date === DAY
          ? [{ id: 1, plan_date, task_id: 3, title: 'Brief', minutes: 40, planned_minutes: 30, finished_at: '' }]
          : [],
    }))
    const { result, rerender } = renderHook(({ day }) => useFocusSessionLog(day), {
      initialProps: { day: DAY },
    })
    await waitFor(() => expect(result.current.spent).toBe(40))
    rerender({ day: '2026-06-21' })
    expect(result.current.finished).toEqual([]) // no frame of yesterday's receipts
    await waitFor(() => expect(mockGetLog).toHaveBeenCalledWith('2026-06-21'))
    rerender({ day: DAY })
    await waitFor(() => expect(result.current.spent).toBe(40))
  })
})

describe('the one-time localStorage move', () => {
  it('uploads the old log and clock, then clears the keys', async () => {
    localStorage.setItem(
      'focus.session',
      JSON.stringify({ date: DAY, finished: [{ taskId: 3, title: 'Brief', minutes: 40, plannedMinutes: 30 }] }),
    )
    localStorage.setItem(
      'focus.clock',
      JSON.stringify({ taskId: 7, date: DAY, state: 'paused', startedAt: 0, accumulatedMs: 6 * MINUTE }),
    )
    // After the move the server holds what the browser used to.
    mockGetClock.mockResolvedValueOnce(null).mockResolvedValue(serverClock({ state: 'paused', elapsed_ms: 6 * MINUTE }))

    const { result } = renderHook(() => ({ log: useFocusSessionLog(DAY), clock: useFocusClock(7, DAY) }))

    await waitFor(() => expect(result.current.clock.run).toBe('paused'))
    expect(mockFinish).toHaveBeenCalledWith(DAY, 3, 40, 30)
    expect(mockSetClock).toHaveBeenCalledWith(DAY, 7, 'paused', 6 * MINUTE)
    expect(result.current.clock.elapsed).toBe(6)
    expect(localStorage.getItem('focus.session')).toBeNull()
    expect(localStorage.getItem('focus.clock')).toBeNull()
  })

  it('does not overwrite a clock the server already has', async () => {
    localStorage.setItem(
      'focus.clock',
      JSON.stringify({ taskId: 7, date: DAY, state: 'paused', startedAt: 0, accumulatedMs: 6 * MINUTE }),
    )
    mockGetClock.mockResolvedValue(serverClock({ task_id: 9 }))
    renderHook(() => useFocusClock(7, DAY))
    await waitFor(() => expect(localStorage.getItem('focus.clock')).toBeNull())
    expect(mockSetClock).not.toHaveBeenCalled()
  })

  it('keeps the keys when the server is unreachable, so the next load retries', async () => {
    localStorage.setItem(
      'focus.session',
      JSON.stringify({ date: DAY, finished: [{ taskId: 3, title: 'Brief', minutes: 40, plannedMinutes: 30 }] }),
    )
    mockFinish.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    renderHook(() => useFocusSessionLog(DAY))
    await waitFor(() => expect(mockGetLog).toHaveBeenCalled())
    expect(localStorage.getItem('focus.session')).not.toBeNull()
  })
})
