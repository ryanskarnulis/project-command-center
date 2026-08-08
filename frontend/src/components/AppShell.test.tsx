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

    // The brand mark is the home link; there is no sidebar. Scoped to the
    // topbar's nav because the phone-width bottom bar carries the same landmark
    // label — only one of the two is ever displayed (see responsive.css).
    expect(screen.getByRole('link', { name: 'Command Center' })).toBeInTheDocument()
    const nav = document.querySelector('.topbar .shell-nav') as HTMLElement
    expect(nav).not.toBeNull()
    expect(within(nav).getByRole('link', { name: 'Focus' })).toBeInTheDocument()
    // Neither Projects nor Tasks is a destination: projects are reached from the
    // dashboard board, and tasks from a project's own Tasks tab.
    expect(within(nav).queryByRole('link', { name: 'Projects' })).not.toBeInTheDocument()
    expect(within(nav).queryByRole('link', { name: 'Tasks' })).not.toBeInTheDocument()
    expect(document.querySelector('.app-sidebar')).not.toBeInTheDocument()
  })

  it('carries a phone-width bottom bar with Home and Agent', () => {
    render(
      <MemoryRouter>
        <AppShell>
          <main>Page</main>
        </AppShell>
      </MemoryRouter>,
    )

    const bottom = document.querySelector('.bottom-nav') as HTMLElement
    expect(bottom).not.toBeNull()
    expect(within(bottom).getByRole('link', { name: 'Home' })).toBeInTheDocument()
    expect(within(bottom).getByRole('link', { name: 'Agent' })).toBeInTheDocument()
    expect(within(bottom).queryByRole('link', { name: 'Tasks' })).not.toBeInTheDocument()
    // Focus is deliberately absent — it lives in the dashboard title row.
    expect(within(bottom).queryByRole('link', { name: 'Focus' })).not.toBeInTheDocument()
  })

  it('shows the summed trash count beside the Trash link', async () => {
    mockGetTrashCount.mockResolvedValue({ projects: 2, tasks: 1, purge_total: 3 })

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
    mockGetTrashCount.mockResolvedValue({ projects: 0, tasks: 0, purge_total: 0 })

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
    mockGetTrashCount.mockResolvedValue({ projects: 0, tasks: 0, purge_total: 0 })

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
