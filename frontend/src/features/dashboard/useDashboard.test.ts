import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getDashboard } from '../../api/dashboard'
import { listAllTasks } from '../../api/tasks'
import type { DashboardOverview } from '../../types/dashboard'
import type { Task } from '../../types/task'
import { useDashboard } from './useDashboard'

vi.mock('../../api/dashboard', () => ({
  getDashboard: vi.fn(),
  getProjectSummary: vi.fn(),
}))

vi.mock('../../api/tasks', () => ({
  listAllTasks: vi.fn(),
}))

const mockGetDashboard = vi.mocked(getDashboard)
const mockListAllTasks = vi.mocked(listAllTasks)

const overview: DashboardOverview = {
  total_open_tasks: 0,
  projects: [],
  recent_inbox: [],
}

describe('useDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetDashboard.mockResolvedValue(overview)
    mockListAllTasks.mockResolvedValue([])
  })

  it('refetches dashboard + tasks when reload() is called', async () => {
    const { result } = renderHook(() => useDashboard())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(mockGetDashboard).toHaveBeenCalledTimes(1)
    expect(mockListAllTasks).toHaveBeenCalledTimes(1)

    act(() => result.current.reload())

    await waitFor(() => expect(mockGetDashboard).toHaveBeenCalledTimes(2))
    expect(mockListAllTasks).toHaveBeenCalledTimes(2)
  })

  it('keeps loading false on refetch and toggles refreshing instead', async () => {
    let resolveSecond!: (tasks: Task[]) => void
    mockListAllTasks
      .mockResolvedValueOnce([])
      .mockImplementationOnce(
        () => new Promise<Task[]>((resolve) => (resolveSecond = resolve)),
      )

    const { result } = renderHook(() => useDashboard())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.refreshing).toBe(false)

    act(() => result.current.reload())

    // Mid-flight the page never flashes its initial spinner: loading stays
    // false and the background refetch surfaces through `refreshing`.
    await waitFor(() => expect(result.current.refreshing).toBe(true))
    expect(result.current.loading).toBe(false)

    await act(async () => {
      resolveSecond([])
    })

    await waitFor(() => expect(result.current.refreshing).toBe(false))
    expect(result.current.loading).toBe(false)
  })
})
