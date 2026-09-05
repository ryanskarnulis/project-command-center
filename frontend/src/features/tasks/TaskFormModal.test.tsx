import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { TaskFormModal } from './TaskFormModal'

afterEach(cleanup)

const projects: Project[] = [
  {
    id: 1,
    name: 'General',
    description: null,
    system_key: 'general',
    sort_order: 0,
    is_protected: true,
    created_at: '2026-06-01T00:00:00',
    updated_at: '2026-06-01T00:00:00',
  },
]

const parent: Task = {
  id: 7,
  project_id: 1,
  parent_task_id: null,
  title: 'Release checklist',
  description: null,
  workflow_status: 'open',
  priority: 'medium',
  due_date: null,
  deferred_until: null,
  estimated_minutes: 90,
  repeat_interval: null,
  recurrence_id: null,
  next_occurrence_date: null,
  created_at: '2026-06-01T00:00:00',
  updated_at: '2026-06-01T00:00:00',
  is_blocked: false,
  is_blocking: false,
  blocked_task_count: 0,
  has_subtasks: true,
  subtask_status: 'open',
}

describe('TaskFormModal', () => {
  it('re-enables Save after an invalid estimate is corrected, without remounting', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(
      <TaskFormModal
        mode="create"
        tasks={[]}
        projects={projects}
        onSave={onSave}
        onClose={vi.fn()}
      />
    )

    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Write tests' } })
    fireEvent.change(screen.getByLabelText('Estimate'), { target: { value: 'later' } })

    const save = screen.getByRole('button', { name: 'Save' })
    fireEvent.click(save)

    expect(await screen.findByText(/Use something like 30m/)).toBeTruthy()
    expect(onSave).not.toHaveBeenCalled()
    expect((save as HTMLButtonElement).disabled).toBe(false)

    fireEvent.change(screen.getByLabelText('Estimate'), { target: { value: '2h' } })
    fireEvent.click(save)

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
    expect(onSave.mock.calls[0][0]).toMatchObject({
      title: 'Write tests',
      estimated_minutes: 120,
    })
  })

  // A parent owns Open / In progress while its subtasks are untouched; Done and
  // the estimate are theirs, so the form neither offers nor sends them.
  it('lets a task with subtasks be started, leaving Done and the estimate to them', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(
      <TaskFormModal
        mode="edit"
        task={parent}
        tasks={[parent]}
        projects={projects}
        onSave={onSave}
        onClose={vi.fn()}
      />
    )

    const status = screen.getByLabelText('Status') as HTMLSelectElement
    expect(status.disabled).toBe(false)
    expect((screen.getByRole('option', { name: 'done' }) as HTMLOptionElement).disabled).toBe(true)
    expect((screen.getByRole('option', { name: 'in progress' }) as HTMLOptionElement).disabled).toBe(false)
    expect((screen.getByLabelText('Estimate') as HTMLInputElement).disabled).toBe(true)

    fireEvent.change(status, { target: { value: 'in_progress' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
    expect(onSave.mock.calls[0][0]).toBe(7)
    expect(onSave.mock.calls[0][1]).toMatchObject({ workflow_status: 'in_progress' })
    expect(onSave.mock.calls[0][1]).not.toHaveProperty('estimated_minutes')
  })

  it('leaves status out of the payload once every subtask is done', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    const finished: Task = { ...parent, workflow_status: 'done', subtask_status: 'done' }
    render(
      <TaskFormModal
        mode="edit"
        task={finished}
        tasks={[finished]}
        projects={projects}
        onSave={onSave}
        onClose={vi.fn()}
      />
    )

    const status = screen.getByLabelText('Status') as HTMLSelectElement
    expect(status.disabled).toBe(true)
    expect(status).toHaveAttribute('title', 'Reopen a subtask to reopen it')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
    expect(onSave.mock.calls[0][1]).not.toHaveProperty('workflow_status')
  })
})
