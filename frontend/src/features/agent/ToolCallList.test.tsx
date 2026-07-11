import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { deleteTask } from '../../api/tasks'
import type { ToolCallRecord } from '../../types/agent'
import { ToolCallList } from './ToolCallList'

vi.mock('../../api/tasks', () => ({
  deleteTask: vi.fn(),
  markTaskDone: vi.fn(),
  reopenTask: vi.fn(),
  restoreTask: vi.fn(),
}))

vi.mock('../../api/projects', () => ({
  deleteProject: vi.fn(),
  restoreProject: vi.fn(),
}))

function renderList(records: ToolCallRecord[]) {
  return render(
    <MemoryRouter>
      <ToolCallList messageId={1} records={records} />
    </MemoryRouter>,
  )
}

describe('ToolCallList', () => {
  it('links mutation rows to the entity they touched', () => {
    renderList([
      {
        tool: 'create_task',
        arguments: { data: { title: 'Water plants' } },
        result: JSON.stringify({ id: 7, title: 'Water plants' }),
        error: null,
      },
      {
        tool: 'search',
        arguments: { query: 'plants' },
        result: '{"projects": [], "tasks": []}',
        error: null,
      },
    ])

    const link = screen.getByRole('link', { name: 'Created task “Water plants”' })
    expect(link).toHaveAttribute('href', '/tasks/7')
    // The read row has nothing to open, so it stays plain text.
    expect(
      screen.queryByRole('link', { name: 'Searched for “plants”' }),
    ).not.toBeInTheDocument()
  })

  it('repoints an undone create at the trash instead of a 404', async () => {
    vi.mocked(deleteTask).mockResolvedValue(undefined as never)
    renderList([
      {
        tool: 'create_task',
        arguments: { data: { title: 'Water plants' } },
        result: JSON.stringify({ id: 7, title: 'Water plants' }),
        error: null,
      },
    ])

    fireEvent.click(screen.getByRole('button', { name: 'Undo (move to trash)' }))
    expect(await screen.findByText('Undone')).toBeInTheDocument()
    expect(vi.mocked(deleteTask)).toHaveBeenCalledWith(7)

    const link = screen.getByRole('link', { name: 'Created task “Water plants”' })
    expect(link).toHaveAttribute('href', '/trash')
  })

  it('repoints an undone trash back at the restored task', async () => {
    const { restoreTask } = await import('../../api/tasks')
    vi.mocked(restoreTask).mockResolvedValue(undefined as never)
    renderList([
      {
        tool: 'trash_task',
        arguments: { task_id: 9 },
        result: 'Task 9 "X" moved to trash (undo with restore_task)',
        error: null,
      },
    ])

    expect(
      screen.getByRole('link', { name: 'Moved a task to the trash' }),
    ).toHaveAttribute('href', '/trash')

    fireEvent.click(screen.getByRole('button', { name: 'Undo (restore)' }))
    expect(await screen.findByText('Undone')).toBeInTheDocument()

    expect(
      screen.getByRole('link', { name: 'Moved a task to the trash' }),
    ).toHaveAttribute('href', '/tasks/9')
  })
})
