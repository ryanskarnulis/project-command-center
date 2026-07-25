import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { listCompletedTasks } from '../../api/tasks'
import type { Task } from '../../types/task'
import { useCompletedTasks } from './useCompletedTasks'

vi.mock('../../api/tasks', () => ({
  listCompletedTasks: vi.fn(),
  reopenTask: vi.fn(),
}))

const mockListCompletedTasks = vi.mocked(listCompletedTasks)

describe('useCompletedTasks', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('drops the previous project’s completed tasks while a new request is in flight', async () => {
    const projectOneTask = { id: 1, title: 'Done in project 1', project_id: 1 } as Task
    let resolveProjectTwo!: (tasks: Task[]) => void
    mockListCompletedTasks.mockImplementation((projectId?: number) =>
      projectId === 1
        ? Promise.resolve([projectOneTask])
        : new Promise<Task[]>((resolve) => (resolveProjectTwo = resolve)),
    )

    const { result, rerender } = renderHook(
      ({ id }: { id: number }) => useCompletedTasks(id),
      { initialProps: { id: 1 } },
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.tasks).toEqual([projectOneTask])

    rerender({ id: 2 })

    // Project 1's completed rows must not render (or stay actionable) under
    // project 2 while its request is still pending.
    expect(result.current.loading).toBe(true)
    expect(result.current.tasks).toEqual([])

    const projectTwoTask = { id: 2, title: 'Done in project 2', project_id: 2 } as Task
    await act(async () => {
      resolveProjectTwo([projectTwoTask])
    })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.tasks).toEqual([projectTwoTask])
  })

  it('keeps stale completed tasks hidden when the new project request fails', async () => {
    const projectOneTask = { id: 1, title: 'Done in project 1', project_id: 1 } as Task
    mockListCompletedTasks.mockImplementation((projectId?: number) =>
      projectId === 1 ? Promise.resolve([projectOneTask]) : Promise.reject(new Error('boom')),
    )

    const { result, rerender } = renderHook(
      ({ id }: { id: number }) => useCompletedTasks(id),
      { initialProps: { id: 1 } },
    )
    await waitFor(() => expect(result.current.tasks).toEqual([projectOneTask]))

    rerender({ id: 2 })

    await waitFor(() => expect(result.current.error).toBe('boom'))
    expect(result.current.tasks).toEqual([])
  })
})
