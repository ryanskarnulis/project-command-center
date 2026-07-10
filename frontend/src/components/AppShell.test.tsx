import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { getTrashCount } from '../api/trash'
import { TrashCountProvider } from '../features/trash/TrashCountContext'
import { AppShell } from './AppShell'

vi.mock('../api/trash', () => ({ getTrashCount: vi.fn() }))
const mockGetTrashCount = vi.mocked(getTrashCount)

describe('AppShell', () => {
  it('exposes the primary routes in the topbar', () => {
    render(
      <MemoryRouter>
        <AppShell>
          <main>Page</main>
        </AppShell>
      </MemoryRouter>,
    )

    // The brand mark is the home link; there is no sidebar.
    expect(screen.getByRole('link', { name: 'Command Center' })).toBeInTheDocument()
    const nav = screen.getByLabelText('Primary navigation')
    expect(within(nav).getByRole('link', { name: 'Focus' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Tasks' })).toBeInTheDocument()
    expect(within(nav).queryByRole('link', { name: 'Projects' })).not.toBeInTheDocument()
    expect(document.querySelector('.app-sidebar')).not.toBeInTheDocument()
  })

  it('shows the summed trash count beside the Trash link', async () => {
    mockGetTrashCount.mockResolvedValue({ projects: 2, tasks: 1 })

    render(
      <MemoryRouter>
        <TrashCountProvider>
          <AppShell>
            <main>Page</main>
          </AppShell>
        </TrashCountProvider>
      </MemoryRouter>,
    )

    expect(
      await screen.findByRole('link', { name: 'Trash (3 items)' }),
    ).toBeInTheDocument()
  })

  it('hides the trash badge when the trash is empty', async () => {
    mockGetTrashCount.mockResolvedValue({ projects: 0, tasks: 0 })

    render(
      <MemoryRouter>
        <TrashCountProvider>
          <AppShell>
            <main>Page</main>
          </AppShell>
        </TrashCountProvider>
      </MemoryRouter>,
    )

    await waitFor(() => expect(mockGetTrashCount).toHaveBeenCalled())
    expect(screen.getByRole('link', { name: 'Trash' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Trash \(/ })).not.toBeInTheDocument()
  })

  it('renders no workspace-status chrome or fake sync/focus controls', async () => {
    mockGetTrashCount.mockResolvedValue({ projects: 0, tasks: 0 })

    render(
      <MemoryRouter>
        <TrashCountProvider>
          <AppShell>
            <main>Page</main>
          </AppShell>
        </TrashCountProvider>
      </MemoryRouter>,
    )

    expect(screen.queryByText('Local workspace')).not.toBeInTheDocument()
    expect(
      screen.queryByText('Local-first workspace. No cloud sync configured.'),
    ).not.toBeInTheDocument()
    expect(screen.queryByText('Local')).not.toBeInTheDocument()
    expect(screen.queryByText('Focus mode')).not.toBeInTheDocument()
    expect(screen.queryByText('Last synced just now')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Notifications' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Customize' })).not.toBeInTheDocument()
  })
})
