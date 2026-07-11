import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getProjectActivity } from '../../api/projects'
import type { ActivityEvent } from '../../types/project'
import { ActivityFeed } from './ActivityFeed'

vi.mock('../../api/projects', () => ({ getProjectActivity: vi.fn() }))

const mockGetProjectActivity = vi.mocked(getProjectActivity)

const events: ActivityEvent[] = [
  {
    id: 1,
    project_id: 7,
    entity_type: 'task',
    entity_id: 42,
    action: 'created',
    summary: 'Task "Fix VPN" created',
    actor: null,
    created_at: '2026-06-30T12:00:00Z',
  },
  {
    id: 2,
    project_id: 7,
    entity_type: 'task',
    entity_id: 42,
    action: 'completed',
    summary: 'Task "Fix VPN" completed',
    actor: 'agent:mcp',
    created_at: '2026-06-30T13:00:00Z',
  },
]

describe('ActivityFeed', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetProjectActivity.mockResolvedValue(events)
  })

  it('is collapsed by default and does not fetch until expanded', () => {
    render(<ActivityFeed projectId={7} />)

    expect(screen.getByRole('button', { name: /activity/i })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    expect(mockGetProjectActivity).not.toHaveBeenCalled()
  })

  it('expands to show the project events', async () => {
    render(<ActivityFeed projectId={7} />)

    await userEvent.click(screen.getByRole('button', { name: /activity/i }))

    expect(await screen.findByText('Task "Fix VPN" created')).toBeInTheDocument()
    expect(screen.getByText('Task "Fix VPN" completed')).toBeInTheDocument()
    expect(mockGetProjectActivity).toHaveBeenCalledWith(7)
  })

  it('shows the empty state when the project has no events', async () => {
    mockGetProjectActivity.mockResolvedValue([])
    render(<ActivityFeed projectId={7} />)

    await userEvent.click(screen.getByRole('button', { name: /activity/i }))

    expect(await screen.findByText('No activity yet.')).toBeInTheDocument()
  })

  it('surfaces a load failure as an alert', async () => {
    mockGetProjectActivity.mockRejectedValue(new Error('backend unreachable'))
    render(<ActivityFeed projectId={7} />)

    await userEvent.click(screen.getByRole('button', { name: /activity/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('backend unreachable')
  })

  it('re-fetches when refreshKey changes', async () => {
    const { rerender } = render(<ActivityFeed projectId={7} refreshKey={0} />)
    await userEvent.click(screen.getByRole('button', { name: /activity/i }))
    await screen.findByText('Task "Fix VPN" created')

    rerender(<ActivityFeed projectId={7} refreshKey={1} />)

    await waitFor(() => expect(mockGetProjectActivity).toHaveBeenCalledTimes(2))
  })
})
