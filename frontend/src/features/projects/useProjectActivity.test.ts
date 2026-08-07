import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getProjectActivity } from '../../api/projects'
import type { ActivityEvent } from '../../types/project'
import { useProjectActivity } from './useProjectActivity'

vi.mock('../../api/projects', () => ({ getProjectActivity: vi.fn() }))

const mockGetProjectActivity = vi.mocked(getProjectActivity)

function event(id: number, projectId: number): ActivityEvent {
  return {
    id,
    project_id: projectId,
    entity_type: 'task',
    entity_id: id,
    action: 'created',
    summary: `Task ${id} created in project ${projectId}`,
    actor: null,
    created_at: '2026-06-30T12:00:00Z',
  }
}

const projectThreeEvent = event(1, 3)
const projectSevenEvent = event(2, 7)

describe('useProjectActivity', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('drops the previous project’s events while the new project’s request is in flight', async () => {
    let resolveProjectSeven!: (events: ActivityEvent[]) => void
    mockGetProjectActivity.mockImplementation((id: number) =>
      id === 3
        ? Promise.resolve([projectThreeEvent])
        : new Promise<ActivityEvent[]>((resolve) => (resolveProjectSeven = resolve)),
    )

    const { result, rerender } = renderHook(
      ({ id }: { id: number }) => useProjectActivity(id),
      { initialProps: { id: 3 } },
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.events).toEqual([projectThreeEvent])

    rerender({ id: 7 })

    // Project 3's audit history must not render under project 7, and the feed
    // must admit it is still loading rather than presenting stale rows as done.
    expect(result.current.events).toEqual([])
    expect(result.current.loading).toBe(true)
    expect(result.current.error).toBeNull()

    await act(async () => {
      resolveProjectSeven([projectSevenEvent])
    })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.events).toEqual([projectSevenEvent])
  })

  it('shows the new project’s error without the previous project’s events', async () => {
    mockGetProjectActivity.mockImplementation((id: number) =>
      id === 3
        ? Promise.resolve([projectThreeEvent])
        : Promise.reject(new Error('backend unreachable')),
    )

    const { result, rerender } = renderHook(
      ({ id }: { id: number }) => useProjectActivity(id),
      { initialProps: { id: 3 } },
    )
    await waitFor(() => expect(result.current.events).toEqual([projectThreeEvent]))

    rerender({ id: 7 })

    await waitFor(() => expect(result.current.error).toBe('backend unreachable'))
    expect(result.current.events).toEqual([])
    expect(result.current.loading).toBe(false)
  })

  it('does not surface the previous project’s error under the new project', async () => {
    mockGetProjectActivity.mockImplementation((id: number) =>
      id === 3
        ? Promise.reject(new Error('backend unreachable'))
        : new Promise<ActivityEvent[]>(() => {}),
    )

    const { result, rerender } = renderHook(
      ({ id }: { id: number }) => useProjectActivity(id),
      { initialProps: { id: 3 } },
    )
    await waitFor(() => expect(result.current.error).toBe('backend unreachable'))

    rerender({ id: 7 })

    expect(result.current.error).toBeNull()
    expect(result.current.loading).toBe(true)
  })

  it('keeps the same project’s events visible while a refreshKey bump re-fetches', async () => {
    const newerEvent = event(3, 3)
    let resolveRefresh!: (events: ActivityEvent[]) => void
    mockGetProjectActivity
      .mockResolvedValueOnce([projectThreeEvent])
      .mockImplementationOnce(
        () => new Promise<ActivityEvent[]>((resolve) => (resolveRefresh = resolve)),
      )

    const { result, rerender } = renderHook(
      ({ id, key }: { id: number; key: number }) => useProjectActivity(id, key),
      { initialProps: { id: 3, key: 0 } },
    )
    await waitFor(() => expect(result.current.events).toEqual([projectThreeEvent]))

    rerender({ id: 3, key: 1 })

    // Same scope: the feed the user is reading must not blank out mid-refresh.
    await waitFor(() => expect(mockGetProjectActivity).toHaveBeenCalledTimes(2))
    expect(result.current.events).toEqual([projectThreeEvent])
    expect(result.current.loading).toBe(false)

    await act(async () => {
      resolveRefresh([projectThreeEvent, newerEvent])
    })

    expect(result.current.events).toEqual([projectThreeEvent, newerEvent])
  })
})
