import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createUnscopedTask, listAllTasks } from '../../api/tasks'
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
})
