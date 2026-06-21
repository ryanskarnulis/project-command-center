import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { search } from '../../api/search'
import type { SearchResults } from '../../types/search'
import { CommandSearch } from './CommandSearch'

vi.mock('../../api/search', () => ({ search: vi.fn() }))
const mockSearch = vi.mocked(search)

const RESULTS: SearchResults = {
  projects: [
    { kind: 'project', id: 7, title: 'Firewall Upgrade', subtitle: null, project_id: null },
  ],
  tasks: [
    { kind: 'task', id: 12, title: 'Audit rules', subtitle: 'Firewall Upgrade', project_id: 7 },
  ],
  inbox_items: [],
}

function LocationProbe() {
  return <span data-testid="location">{useLocation().pathname}</span>
}

function renderBar() {
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <CommandSearch />
      <Routes>
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('CommandSearch', () => {
  it('shows grouped results and navigates on click', async () => {
    mockSearch.mockResolvedValue(RESULTS)
    const user = userEvent.setup()
    renderBar()

    await user.type(
      screen.getByRole('combobox', { name: /search projects/i }),
      'firewall',
    )

    // "Audit rules" is unique; the project name appears twice (title + task subtitle).
    expect(await screen.findByText('Audit rules')).toBeInTheDocument()
    expect(screen.getAllByText('Firewall Upgrade')).toHaveLength(2)
    // Grouped: a Project kind badge and a Task kind badge are present.
    expect(screen.getByText('Project')).toBeInTheDocument()
    expect(screen.getByText('Task')).toBeInTheDocument()

    await user.click(screen.getByText('Audit rules'))

    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/tasks/12'),
    )
  })

  it('shows an empty state when nothing matches', async () => {
    mockSearch.mockResolvedValue({ projects: [], tasks: [], inbox_items: [] })
    const user = userEvent.setup()
    renderBar()

    await user.type(
      screen.getByRole('combobox', { name: /search projects/i }),
      'zzz',
    )

    expect(await screen.findByText(/No matches for/)).toBeInTheDocument()
  })
})
