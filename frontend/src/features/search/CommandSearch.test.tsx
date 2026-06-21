import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { search } from '../../api/search'
import { createInbox, processInbox } from '../../api/inbox'
import { markTaskDone } from '../../api/tasks'
import type { SearchResults } from '../../types/search'
import { CommandSearch } from './CommandSearch'

vi.mock('../../api/search', () => ({ search: vi.fn() }))
vi.mock('../../api/inbox', () => ({
  createInbox: vi.fn(),
  processInbox: vi.fn(),
}))
vi.mock('../../api/tasks', () => ({ markTaskDone: vi.fn() }))

const mockSearch = vi.mocked(search)
const mockCreateInbox = vi.mocked(createInbox)
const mockProcessInbox = vi.mocked(processInbox)
const mockMarkTaskDone = vi.mocked(markTaskDone)

const RESULTS: SearchResults = {
  projects: [
    {
      kind: 'project',
      id: 7,
      title: 'Firewall Upgrade',
      subtitle: null,
      project_id: null,
      review_status: null,
      workflow_status: null,
    },
  ],
  tasks: [
    {
      kind: 'task',
      id: 12,
      title: 'Audit rules',
      subtitle: 'Firewall Upgrade',
      project_id: 7,
      review_status: 'accepted',
      workflow_status: 'open',
    },
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
  beforeEach(() => {
    vi.clearAllMocks()
  })

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

  it('/new captures the text, runs extraction, and navigates to the inbox item', async () => {
    mockCreateInbox.mockResolvedValue({ id: 99 } as Awaited<
      ReturnType<typeof createInbox>
    >)
    mockProcessInbox.mockResolvedValue([])
    const user = userEvent.setup()
    renderBar()

    await user.type(
      screen.getByRole('combobox', { name: /search projects/i }),
      '/new buy milk',
    )

    // The confirm row reflects the captured text; search is never called for /new.
    await user.click(await screen.findByText(/Capture & extract: buy milk/))

    await waitFor(() =>
      expect(mockCreateInbox).toHaveBeenCalledWith({ raw_text: 'buy milk' }),
    )
    expect(mockProcessInbox).toHaveBeenCalledWith(99)
    expect(mockSearch).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/inbox/99'),
    )
  })

  it('/done lists only open tasks and completes the selected one', async () => {
    mockSearch.mockResolvedValue({
      projects: [],
      tasks: [
        {
          kind: 'task',
          id: 12,
          title: 'Audit rules',
          subtitle: 'Firewall Upgrade',
          project_id: 7,
          review_status: 'accepted',
          workflow_status: 'open',
        },
        {
          kind: 'task',
          id: 13,
          title: 'Already finished',
          subtitle: null,
          project_id: 7,
          review_status: 'accepted',
          workflow_status: 'done',
        },
        {
          kind: 'task',
          id: 14,
          title: 'Just a candidate',
          subtitle: null,
          project_id: 7,
          review_status: 'candidate',
          workflow_status: 'open',
        },
      ],
      inbox_items: [],
    })
    mockMarkTaskDone.mockResolvedValue({ id: 12 } as Awaited<
      ReturnType<typeof markTaskDone>
    >)
    const user = userEvent.setup()
    renderBar()

    await user.type(
      screen.getByRole('combobox', { name: /search projects/i }),
      '/done audit',
    )

    // Open + accepted task is offered; done and candidate tasks are filtered out.
    expect(await screen.findByText('Audit rules')).toBeInTheDocument()
    expect(screen.queryByText('Already finished')).not.toBeInTheDocument()
    expect(screen.queryByText('Just a candidate')).not.toBeInTheDocument()

    await user.click(screen.getByText('Audit rules'))

    await waitFor(() => expect(mockMarkTaskDone).toHaveBeenCalledWith(12))
  })

  it('focuses and selects the input on Cmd+K and opens the dropdown', async () => {
    mockSearch.mockResolvedValue(RESULTS)
    const user = userEvent.setup()
    renderBar()

    const input = screen.getByRole('combobox', { name: /search projects/i })
    expect(input).not.toHaveFocus()

    fireEvent.keyDown(window, { key: 'k', metaKey: true })

    expect(input).toHaveFocus()

    // The shortcut opened the bar; typing now surfaces the result listbox.
    await user.type(input, 'firewall')
    expect(await screen.findByText('Audit rules')).toBeInTheDocument()
    expect(screen.getByRole('listbox')).toBeInTheDocument()
  })

  it('also responds to Ctrl+K (non-mac) but not a bare k', async () => {
    renderBar()
    const input = screen.getByRole('combobox', { name: /search projects/i })

    // A bare "k" must not hijack focus into the bar.
    fireEvent.keyDown(window, { key: 'k' })
    expect(input).not.toHaveFocus()

    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
    expect(input).toHaveFocus()
  })

  it('shows command hints for a bare slash', async () => {
    const user = userEvent.setup()
    renderBar()

    await user.type(
      screen.getByRole('combobox', { name: /search projects/i }),
      '/',
    )

    expect(await screen.findByText('/new')).toBeInTheDocument()
    expect(screen.getByText('/done')).toBeInTheDocument()
    expect(mockSearch).not.toHaveBeenCalled()
  })
})
