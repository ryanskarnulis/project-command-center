import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { getTrashCount } from '../api/trash'
import { TrashCountProvider } from '../features/trash/TrashCountContext'
import { AppShell } from './AppShell'

vi.mock('../api/trash', () => ({ getTrashCount: vi.fn() }))
const mockGetTrashCount = vi.mocked(getTrashCount)

describe('AppShell', () => {
  it('keeps Inbox and Tasks out of the left navigation', () => {
    render(
      <MemoryRouter>
        <AppShell>
          <main>Page</main>
        </AppShell>
      </MemoryRouter>,
    )

    const nav = screen.getByLabelText('Primary navigation')
    expect(within(nav).queryByRole('link', { name: 'Inbox' })).not.toBeInTheDocument()
    expect(within(nav).queryByRole('link', { name: 'Tasks' })).not.toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Command Center' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Projects' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Training' })).toBeInTheDocument()
  })

  it('shows the summed trash count beside the Trash link', async () => {
    mockGetTrashCount.mockResolvedValue({ projects: 2, tasks: 1, inbox_items: 0, training_examples: 0 })

    render(
      <MemoryRouter>
        <TrashCountProvider>
          <AppShell>
            <main>Page</main>
          </AppShell>
        </TrashCountProvider>
      </MemoryRouter>,
    )

    const nav = screen.getByLabelText('Primary navigation')
    expect(
      await within(nav).findByRole('link', { name: 'Trash (3 items)' }),
    ).toBeInTheDocument()
  })

  it('hides the trash badge when the trash is empty', async () => {
    mockGetTrashCount.mockResolvedValue({ projects: 0, tasks: 0, inbox_items: 0, training_examples: 0 })

    render(
      <MemoryRouter>
        <TrashCountProvider>
          <AppShell>
            <main>Page</main>
          </AppShell>
        </TrashCountProvider>
      </MemoryRouter>,
    )

    const nav = screen.getByLabelText('Primary navigation')
    await waitFor(() => expect(mockGetTrashCount).toHaveBeenCalled())
    expect(within(nav).getByRole('link', { name: 'Trash' })).toBeInTheDocument()
    expect(within(nav).queryByRole('link', { name: /Trash \(/ })).not.toBeInTheDocument()
  })

  it('shows honest local workspace status instead of fake sync or focus controls', async () => {
    mockGetTrashCount.mockResolvedValue({ projects: 0, tasks: 0, inbox_items: 0, training_examples: 0 })

    render(
      <MemoryRouter>
        <TrashCountProvider>
          <AppShell>
            <main>Page</main>
          </AppShell>
        </TrashCountProvider>
      </MemoryRouter>,
    )

    expect(screen.getByRole('region', { name: 'Workspace status' })).toHaveTextContent(
      'Local workspace',
    )
    expect(screen.getByText('Local-first workspace. No cloud sync configured.')).toBeInTheDocument()
    expect(screen.queryByText('Focus mode')).not.toBeInTheDocument()
    expect(screen.queryByText('Last synced just now')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Notifications' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Customize' })).not.toBeInTheDocument()
  })
})
