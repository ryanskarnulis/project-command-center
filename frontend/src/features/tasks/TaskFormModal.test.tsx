import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Project } from '../../types/project'
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
})
