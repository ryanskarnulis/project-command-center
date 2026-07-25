import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Task } from '../../types/task'
import { SubtaskComposer } from './SubtaskComposer'

afterEach(cleanup)

const parent: Task = {
  id: 1,
  title: 'Parent',
  description: null,
  workflow_status: 'open',
  priority: 'medium',
  due_date: null,
  deferred_until: null,
  estimated_minutes: null,
  project_id: 1,
  parent_task_id: null,
  repeat_interval: null,
  recurrence_id: null,
  next_occurrence_date: null,
  created_at: '2026-06-01T00:00:00',
  updated_at: '2026-06-01T00:00:00',
  is_blocked: false,
  is_blocking: false,
  blocked_task_count: 0,
  has_subtasks: false,
}

describe('SubtaskComposer', () => {
  it('creates once when submitted twice before the promise settles', async () => {
    let resolveCreate: () => void = () => {}
    const onCreate = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveCreate = resolve
        }),
    )
    render(
      <SubtaskComposer parent={parent} onCreate={onCreate} onCancel={vi.fn()} />,
    )

    fireEvent.change(screen.getByPlaceholderText('Subtask title'), {
      target: { value: 'Child' },
    })
    const form = screen.getByPlaceholderText('Subtask title').closest('form')!
    fireEvent.submit(form)
    fireEvent.submit(form)

    expect(onCreate).toHaveBeenCalledTimes(1)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Adding…' })).toBeDisabled(),
    )

    resolveCreate()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Add' })).toBeEnabled(),
    )
    expect(onCreate).toHaveBeenCalledTimes(1)
  })

  it('restores the form when creation fails', async () => {
    const onCreate = vi.fn().mockRejectedValue(new Error('boom'))
    render(
      <SubtaskComposer parent={parent} onCreate={onCreate} onCancel={vi.fn()} />,
    )

    const title = screen.getByPlaceholderText('Subtask title')
    fireEvent.change(title, { target: { value: 'Child' } })
    fireEvent.submit(title.closest('form')!)

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Add' })).toBeEnabled(),
    )
    expect(title).toHaveValue('Child')
    expect(title).toBeEnabled()
  })
})
