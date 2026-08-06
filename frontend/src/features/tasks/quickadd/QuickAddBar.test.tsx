import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Project } from '../../../types/project'
import { QuickAddBar } from './QuickAddBar'

afterEach(cleanup)

function project(id: number, name: string): Project {
  return {
    id,
    name,
    description: null,
    system_key: null,
    sort_order: 0,
    is_protected: false,
    created_at: '2026-06-01T10:00:00Z',
    updated_at: '2026-06-01T10:00:00Z',
  }
}

const projects = [project(1, 'Ops'), project(2, 'Home Lab')]

function setup(props: Partial<Parameters<typeof QuickAddBar>[0]> = {}) {
  const onCreate = vi.fn(() => Promise.resolve())
  const onMoreOptions = vi.fn()
  render(
    <QuickAddBar
      projects={projects}
      onCreate={onCreate}
      onMoreOptions={onMoreOptions}
      {...props}
    />,
  )
  return { onCreate, onMoreOptions, input: screen.getByLabelText('Quick add task') }
}

describe('QuickAddBar', () => {
  it('previews parsed tokens as chips while typing', () => {
    const { input } = setup()
    fireEvent.change(input, { target: { value: 'Renew TLS cert !high #ops ~20m' } })

    expect(screen.getByRole('button', { name: 'Priority: high' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Project: Ops' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Estimate: 20 minutes' })).toBeInTheDocument()
  })

  it('creates the parsed task on submit and clears the input', async () => {
    const { onCreate, input } = setup()
    fireEvent.change(input, { target: { value: 'Renew TLS cert !high #ops ~20m' } })
    fireEvent.submit(input)

    await waitFor(() =>
      expect(onCreate).toHaveBeenCalledExactlyOnceWith({
        title: 'Renew TLS cert',
        priority: 'high',
        due_date: null,
        estimated_minutes: 20,
        project_id: 1,
      }),
    )
    expect(input).toHaveValue('')
  })

  it('keeps the draft and becomes interactive again when creation rejects', async () => {
    const onCreate = () => Promise.reject(new Error('Could not create task'))
    const { input } = setup({ onCreate })
    fireEvent.change(input, { target: { value: 'Keep this draft' } })
    fireEvent.submit(input)

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Add' })).toBeEnabled(),
    )
    expect(input).toHaveValue('Keep this draft')
  })

  it('defaults the project to the page scope when no token is given', async () => {
    const { onCreate, input } = setup({ scopeProjectId: 2 })
    fireEvent.change(input, { target: { value: 'Fix the rack' } })
    fireEvent.submit(input)

    await waitFor(() =>
      expect(onCreate).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ title: 'Fix the rack', project_id: 2 }),
      ),
    )
  })

  it('lets a chip edit override the parsed token', async () => {
    const { onCreate, input } = setup()
    fireEvent.change(input, { target: { value: 'Ship it !high' } })
    fireEvent.click(screen.getByRole('button', { name: 'Priority: high' }))
    fireEvent.click(screen.getByRole('button', { name: 'urgent' }))
    fireEvent.submit(input)

    await waitFor(() =>
      expect(onCreate).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ title: 'Ship it', priority: 'urgent' }),
      ),
    )
  })

  it('commits a chip editor submit without creating the task', async () => {
    // The chip editors are portaled, but React still bubbles their submit
    // through the fiber tree into the quick-add <form>. Unguarded, committing
    // an estimate created the task immediately — with the stale estimate,
    // since the override hadn't re-rendered yet.
    const { onCreate, input } = setup()
    fireEvent.change(input, { target: { value: 'Renew TLS cert ~20m' } })
    fireEvent.click(screen.getByRole('button', { name: 'Estimate: 20 minutes' }))
    fireEvent.change(screen.getByLabelText('Estimate'), { target: { value: '45m' } })
    fireEvent.click(screen.getByRole('button', { name: 'Set' }))

    expect(onCreate).not.toHaveBeenCalled()
    expect(input).toHaveValue('Renew TLS cert ~20m')
    expect(screen.getByRole('button', { name: 'Estimate: 45 minutes' })).toBeInTheDocument()

    fireEvent.submit(input)
    await waitFor(() =>
      expect(onCreate).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ title: 'Renew TLS cert', estimated_minutes: 45 }),
      ),
    )
  })

  it('does not submit a draft with an empty title', () => {
    const { onCreate, input } = setup()
    fireEvent.change(input, { target: { value: '!high' } })
    fireEvent.submit(input)
    expect(onCreate).not.toHaveBeenCalled()
  })

  it('hands the draft to the full editor via More options', () => {
    const { onCreate, onMoreOptions, input } = setup()
    fireEvent.change(input, { target: { value: 'Renew TLS cert !high ~20m' } })
    fireEvent.click(screen.getByRole('button', { name: 'More options' }))

    expect(onMoreOptions).toHaveBeenCalledExactlyOnceWith({
      title: 'Renew TLS cert',
      priority: 'high',
      due_date: null,
      estimated_minutes: 20,
    })
    expect(onCreate).not.toHaveBeenCalled()
    expect(input).toHaveValue('')
  })
})
