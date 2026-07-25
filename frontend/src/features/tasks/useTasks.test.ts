import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createUnscopedTask, listAllTasks, listTasks } from '../../api/tasks'
import type { Task } from '../../types/task'
import { useTasks } from './useTasks'

vi.mock('../../api/tasks', () => ({
  createTask: vi.fn(),
  createUnscopedTask: vi.fn(),
  deleteTask: vi.fn(),
  listAllTasks: vi.fn(),
  listTasks: vi.fn(),
  markTaskDone: vi.fn(),
  skipOccurrence: vi.fn(),
  updateTask: vi.fn(),
}))

const mockCreateUnscopedTask = vi.mocked(createUnscopedTask)
const mockListAllTasks = vi.mocked(listAllTasks)
const mockListTasks = vi.mocked(listTasks)

describe('useTasks', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListAllTasks.mockResolvedValue([])
  })

  it('refetches after a mutation via reload()', async () => {
    mockCreateUnscopedTask.mockResolvedValue({ id: 1 } as Task)
    const { result } = renderHook(() => useTasks())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(mockListAllTasks).toHaveBeenCalledTimes(1)

    await act(async () => {
      await result.current.create({ title: 'New task' })
    })

    await waitFor(() => expect(mockListAllTasks).toHaveBeenCalledTimes(2))
  })

  it('keeps loading false on refetch and toggles refreshing instead', async () => {
    let resolveSecond!: (tasks: Task[]) => void
    mockListAllTasks
      .mockResolvedValueOnce([])
      .mockImplementationOnce(
        () => new Promise<Task[]>((resolve) => (resolveSecond = resolve)),
      )

    const { result } = renderHook(() => useTasks())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.refreshing).toBe(false)

    act(() => result.current.reload())

    // The list stays rendered during a background refetch: loading never flips
    // back on, `refreshing` carries the in-flight state instead.
    await waitFor(() => expect(result.current.refreshing).toBe(true))
    expect(result.current.loading).toBe(false)

    await act(async () => {
      resolveSecond([])
    })

    await waitFor(() => expect(result.current.refreshing).toBe(false))
    expect(result.current.loading).toBe(false)
  })

  it('drops the previous project’s tasks while a new project request is in flight', async () => {
    const projectOneTask = { id: 1, title: 'Project 1 task', project_id: 1 } as Task
    let resolveProjectTwo!: (tasks: Task[]) => void
    mockListTasks.mockImplementation((projectId: number) =>
      projectId === 1
        ? Promise.resolve([projectOneTask])
        : new Promise<Task[]>((resolve) => (resolveProjectTwo = resolve)),
    )

    const { result, rerender } = renderHook(({ id }: { id: number }) => useTasks(id), {
      initialProps: { id: 1 },
    })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.tasks).toEqual([projectOneTask])

    rerender({ id: 2 })

    // Project 1's rows must not render (or be mutable) under project 2 while
    // its request is still pending.
    expect(result.current.tasks).toEqual([])
    expect(result.current.loading).toBe(true)

    const projectTwoTask = { id: 2, title: 'Project 2 task', project_id: 2 } as Task
    await act(async () => {
      resolveProjectTwo([projectTwoTask])
    })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.tasks).toEqual([projectTwoTask])
  })

  it('keeps stale tasks hidden when the new project request fails', async () => {
    const projectOneTask = { id: 1, title: 'Project 1 task', project_id: 1 } as Task
    mockListTasks.mockImplementation((projectId: number) =>
      projectId === 1
        ? Promise.resolve([projectOneTask])
        : Promise.reject(new Error('boom')),
    )

    const { result, rerender } = renderHook(({ id }: { id: number }) => useTasks(id), {
      initialProps: { id: 1 },
    })
    await waitFor(() => expect(result.current.tasks).toEqual([projectOneTask]))

    rerender({ id: 2 })

    await waitFor(() => expect(result.current.error).toBe('boom'))
    expect(result.current.tasks).toEqual([])
  })
})
