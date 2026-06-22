import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getGlobalGantt } from '../../api/planning'
import { updateTask } from '../../api/tasks'
import { ToastProvider } from '../../components/ToastProvider'
import type { Task } from '../../types/task'
import { GlobalPlanningPage } from './GlobalPlanningPage'

vi.mock('../../api/planning', () => ({ getGlobalGantt: vi.fn() }))
vi.mock('../../api/tasks', () => ({ updateTask: vi.fn() }))

const mockGetGantt = vi.mocked(getGlobalGantt)
const mockUpdateTask = vi.mocked(updateTask)

function task(overrides: Partial<Task> & { id: number }): Task {
  return {
    project_id: 1,
    inbox_item_id: null,
    parent_task_id: null,
    title: `Task ${overrides.id}`,
    description: null,
    review_status: 'accepted',
    workflow_status: 'open',
    priority: 'medium',
    due_date: null,
    scheduled_start: null,
    estimated_minutes: null,
    repeat_interval: null,
    recurrence_id: null,
    confidence: null,
    assignee_hint: null,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    is_blocked: false,
    is_blocking: false,
    blocked_task_count: 0,
    has_subtasks: false,
    ...overrides,
  }
}

function renderPage() {
  return render(
    <ToastProvider>
      <MemoryRouter>
        <GlobalPlanningPage />
      </MemoryRouter>
    </ToastProvider>,
  )
}

describe('GlobalPlanningPage', () => {
  beforeEach(() => {
    mockGetGantt.mockResolvedValue({
      tasks: [
        task({ id: 1, project_id: 7, title: 'Fw bar', scheduled_start: '2026-06-20' }),
        task({ id: 2, project_id: 9, title: 'Web bar', scheduled_start: '2026-06-21' }),
      ],
      dependencies: [],
      projects: [
        { id: 7, name: 'Firewall' },
        { id: 9, name: 'Website' },
      ],
    })
    mockUpdateTask.mockResolvedValue(task({ id: 1 }))
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('renders a project legend and a grouped section header per project', async () => {
    renderPage()

    // Legend lists both projects.
    const legend = await screen.findByRole('list', { name: 'Projects' })
    expect(within(legend).getByText('Firewall')).toBeInTheDocument()
    expect(within(legend).getByText('Website')).toBeInTheDocument()

    // A section header before each project's bars (group headers render the name).
    const headers = document.querySelectorAll('.gantt-group-head .gantt-group-name')
    expect([...headers].map((h) => h.textContent)).toEqual(['Firewall', 'Website'])

    // A bar from each project is drawn.
    expect(screen.getAllByText('Fw bar').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Web bar').length).toBeGreaterThan(0)
  })

  it('reschedules via the task PATCH on a bar drag', async () => {
    renderPage()
    await screen.findByRole('list', { name: 'Projects' })

    // jsdom has no layout; stub the column width the drag math measures.
    for (const cell of document.querySelectorAll('.gantt-col-bg')) {
      ;(cell as HTMLElement).getBoundingClientRect = () =>
        ({ width: 30, height: 34, top: 0, left: 0, right: 0, bottom: 0, x: 0, y: 0, toJSON: () => {} }) as DOMRect
    }

    const bar = document.querySelector('[data-bar-id="1"]') as HTMLElement
    bar.setPointerCapture = () => {}
    bar.releasePointerCapture = () => {}
    bar.dispatchEvent(
      new PointerEvent('pointerdown', { clientX: 0, bubbles: true, pointerId: 1 }),
    )
    window.dispatchEvent(
      new PointerEvent('pointermove', { clientX: 60, bubbles: true, pointerId: 1 }),
    )
    window.dispatchEvent(
      new PointerEvent('pointerup', { clientX: 60, bubbles: true, pointerId: 1 }),
    )

    await waitFor(() => {
      expect(mockUpdateTask).toHaveBeenCalledWith(1, {
        scheduled_start: '2026-06-22',
      })
    })
  })
})
