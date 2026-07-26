import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { updateTask } from '../../api/tasks'
import type { Task } from '../../types/task'
import { useScopedTaskUpdate } from './useScopedTaskUpdate'

vi.mock('../../api/tasks', () => ({
  updateTask: vi.fn(),
}))

const mockUpdateTask = vi.mocked(updateTask)

const task = { id: 1, title: 'Task', recurrence_id: null } as Task

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('useScopedTaskUpdate', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('does not let a stale PATCH response overwrite a newer one', async () => {
    const oldResponse = { ...task, priority: 'low' } as Task
    const newResponse = { ...task, priority: 'high' } as Task
    const first = deferred<Task>()
    const second = deferred<Task>()
    mockUpdateTask.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)

    const saved: Task[] = []
    const { result } = renderHook(() =>
      useScopedTaskUpdate(task, (updated) => {
        saved.push(updated)
      }),
    )

    act(() => {
      result.current.savePatch({ priority: 'low' })
    })
    act(() => {
      result.current.savePatch({ priority: 'high' })
    })
    expect(mockUpdateTask).toHaveBeenCalledTimes(2)

    // Newest response lands first, the stale one afterwards.
    await act(async () => {
      second.resolve(newResponse)
      await second.promise
    })
    await act(async () => {
      first.resolve(oldResponse)
      await first.promise
    })

    await waitFor(() => expect(result.current.saveState).toBe('saved'))
    expect(saved).toEqual([newResponse])
    expect(saved[saved.length - 1]).toBe(newResponse)
  })

  it('ignores a stale failure so the newest write keeps the save line', async () => {
    const newResponse = { ...task, priority: 'high' } as Task
    const first = deferred<Task>()
    const second = deferred<Task>()
    mockUpdateTask.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)

    const saved: Task[] = []
    const { result } = renderHook(() =>
      useScopedTaskUpdate(task, (updated) => {
        saved.push(updated)
      }),
    )

    act(() => {
      result.current.savePatch({ priority: 'low' })
    })
    act(() => {
      result.current.savePatch({ priority: 'high' })
    })

    await act(async () => {
      second.resolve(newResponse)
      await second.promise
    })
    await act(async () => {
      first.reject(new Error('boom'))
      await first.promise.catch(() => undefined)
    })

    expect(result.current.saveState).toBe('saved')
    expect(result.current.saveError).toBeNull()
    expect(saved).toEqual([newResponse])
  })

  it('retires an in-flight PATCH when the hook switches to another task', async () => {
    const otherTask = { id: 2, title: 'Other', recurrence_id: null } as Task
    const staleResponse = { ...task, title: 'Task edited' } as Task
    const pending = deferred<Task>()
    mockUpdateTask.mockReturnValueOnce(pending.promise)

    const saved: Task[] = []
    const { result, rerender } = renderHook(
      ({ current }: { current: Task }) =>
        useScopedTaskUpdate(current, (updated) => {
          saved.push(updated)
        }),
      { initialProps: { current: task } },
    )

    act(() => {
      result.current.savePatch({ title: 'Task edited' })
    })
    expect(result.current.saveState).toBe('saving')

    // The detail surface navigates to another task, then the old PATCH lands.
    rerender({ current: otherTask })
    expect(result.current.saveState).toBe('idle')
    await act(async () => {
      pending.resolve(staleResponse)
      await pending.promise
    })

    expect(saved).toEqual([])
    expect(result.current.saveState).toBe('idle')
    expect(result.current.saveError).toBeNull()
  })

  it('clears a pending scope prompt when the task changes', () => {
    const recurring = { ...task, recurrence_id: 'rec-9' } as Task
    const otherTask = { id: 2, title: 'Other', recurrence_id: null } as Task

    const { result, rerender } = renderHook(
      ({ current }: { current: Task }) => useScopedTaskUpdate(current, () => undefined),
      { initialProps: { current: recurring } },
    )

    act(() => {
      result.current.savePatch({ title: 'Renamed series' })
    })
    expect(result.current.scopePromptOpen).toBe(true)
    expect(mockUpdateTask).not.toHaveBeenCalled()

    rerender({ current: otherTask })
    expect(result.current.scopePromptOpen).toBe(false)

    // The retired prompt must not be replayable against the new task.
    act(() => {
      result.current.resolveScope('this')
    })
    expect(mockUpdateTask).not.toHaveBeenCalled()
  })
})
