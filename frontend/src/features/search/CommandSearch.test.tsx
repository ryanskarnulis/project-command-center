import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createConversation, postMessage } from '../../api/agent'
import { search } from '../../api/search'
import type { Conversation, MessageExchange } from '../../types/agent'
import type { SearchResults } from '../../types/search'
import { CommandSearch } from './CommandSearch'

vi.mock('../../api/search', () => ({ search: vi.fn() }))
vi.mock('../../api/agent', () => ({
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  getConversation: vi.fn(),
  listConversations: vi.fn(),
  postMessage: vi.fn(),
}))

const mockSearch = vi.mocked(search)
const mockCreateConversation = vi.mocked(createConversation)
const mockPostMessage = vi.mocked(postMessage)

const RESULTS: SearchResults = {
  projects: [
    {
      kind: 'project',
      id: 7,
      title: 'Firewall Upgrade',
      subtitle: null,
      project_id: null,
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
      workflow_status: 'open',
    },
  ],
}

const CONVERSATION: Conversation = {
  id: 5,
  title: null,
  created_at: '2026-07-11T10:00:00Z',
  updated_at: '2026-07-11T10:00:00Z',
}

const EXCHANGE: MessageExchange = {
  user_message: {
    id: 1,
    conversation_id: 5,
    role: 'user',
    content: 'plan my day',
    tool_calls: null,
    stop_reason: null,
    created_at: '2026-07-11T10:00:00Z',
  },
  assistant_message: {
    id: 2,
    conversation_id: 5,
    role: 'assistant',
    content: 'Here is your plan.',
    tool_calls: null,
    stop_reason: 'completed',
    created_at: '2026-07-11T10:00:05Z',
  },
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

function getInput() {
  return screen.getByRole('combobox', { name: /search projects/i })
}

describe('CommandSearch', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows grouped results and navigates on click', async () => {
    mockSearch.mockResolvedValue(RESULTS)
    const user = userEvent.setup()
    renderBar()

    await user.type(getInput(), 'firewall')

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
    mockSearch.mockResolvedValue({ projects: [], tasks: [] })
    const user = userEvent.setup()
    renderBar()

    await user.type(getInput(), 'zzz')

    expect(await screen.findByText(/No matches for/)).toBeInTheDocument()
  })

  it('Enter on an arrow-highlighted row navigates instead of asking the agent', async () => {
    mockSearch.mockResolvedValue(RESULTS)
    const user = userEvent.setup()
    renderBar()

    await user.type(getInput(), 'firewall')
    await screen.findByText('Audit rules')

    await user.keyboard('{ArrowDown}{Enter}')

    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/projects/7'),
    )
    expect(mockCreateConversation).not.toHaveBeenCalled()
    expect(mockPostMessage).not.toHaveBeenCalled()
  })

  it('plain Enter with no highlighted row asks the agent and renders the exchange inline', async () => {
    mockSearch.mockResolvedValue({ projects: [], tasks: [] })
    mockCreateConversation.mockResolvedValue(CONVERSATION)
    let resolveRun: (value: MessageExchange) => void = () => {}
    mockPostMessage.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRun = resolve
        }),
    )
    const user = userEvent.setup()
    renderBar()

    await user.type(getInput(), 'plan my day')
    // The dropdown offers the ask affordance for discoverability.
    expect(
      await screen.findByRole('button', { name: /Ask the agent/ }),
    ).toBeInTheDocument()

    await user.keyboard('{Enter}')

    // In flight: user bubble + working indicator, input disabled.
    expect(await screen.findByRole('status')).toHaveTextContent(/Working/)
    expect(screen.getByText('plan my day')).toBeInTheDocument()
    expect(getInput()).toBeDisabled()
    await waitFor(() =>
      expect(mockPostMessage).toHaveBeenCalledWith(5, 'plan my day'),
    )

    resolveRun(EXCHANGE)

    // The exchange renders via MessageBubble (assistant markdown included);
    // the bar clears and re-enables for the next search/ask.
    expect(await screen.findByText('Here is your plan.')).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(getInput()).toBeEnabled()
    expect(getInput()).toHaveValue('')

    // Continue in Agent navigates to the persisted conversation.
    await user.click(screen.getByRole('button', { name: /Continue in Agent/ }))
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/agent/5'),
    )
    expect(screen.queryByText('Here is your plan.')).not.toBeInTheDocument()
  })

  it('clicking the ask affordance posts to the agent', async () => {
    mockSearch.mockResolvedValue({ projects: [], tasks: [] })
    mockCreateConversation.mockResolvedValue(CONVERSATION)
    mockPostMessage.mockResolvedValue(EXCHANGE)
    const user = userEvent.setup()
    renderBar()

    await user.type(getInput(), 'plan my day')
    await user.click(await screen.findByRole('button', { name: /Ask the agent/ }))

    expect(await screen.findByText('Here is your plan.')).toBeInTheDocument()
    expect(mockPostMessage).toHaveBeenCalledWith(5, 'plan my day')
  })

  it('surfaces an ask failure inline and re-enables the input', async () => {
    const { ApiError } = await import('../../api/client')
    mockSearch.mockResolvedValue({ projects: [], tasks: [] })
    mockCreateConversation.mockResolvedValue(CONVERSATION)
    mockPostMessage.mockRejectedValue(
      new ApiError(429, { detail: 'rate limit exceeded' }),
    )
    const user = userEvent.setup()
    renderBar()

    await user.type(getInput(), 'plan my day')
    await user.keyboard('{Enter}')

    expect(await screen.findByRole('alert')).toHaveTextContent(/Rate limited/)
    // The input is back and keeps the text so the ask can be retried.
    expect(getInput()).toBeEnabled()
    expect(getInput()).toHaveValue('plan my day')
  })

  it('Escape dismisses the inline exchange', async () => {
    mockSearch.mockResolvedValue({ projects: [], tasks: [] })
    mockCreateConversation.mockResolvedValue(CONVERSATION)
    mockPostMessage.mockResolvedValue(EXCHANGE)
    const user = userEvent.setup()
    renderBar()

    await user.type(getInput(), 'plan my day')
    await user.keyboard('{Enter}')
    expect(await screen.findByText('Here is your plan.')).toBeInTheDocument()

    fireEvent.keyDown(getInput(), { key: 'Escape' })

    expect(screen.queryByText('Here is your plan.')).not.toBeInTheDocument()
  })

  it('treats slash input as a literal search, not a command', async () => {
    mockSearch.mockResolvedValue({ projects: [], tasks: [] })
    const user = userEvent.setup()
    renderBar()

    await user.type(getInput(), '/done foo')

    await waitFor(() =>
      expect(mockSearch).toHaveBeenCalledWith('/done foo', expect.anything()),
    )
    // No command UI — just the normal empty state plus the ask affordance.
    expect(await screen.findByText(/No matches for/)).toBeInTheDocument()
    expect(screen.queryByText(/Complete a task/)).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /Ask the agent/ }),
    ).toBeInTheDocument()
  })

  it('focuses and selects the input on Cmd+K and opens the dropdown', async () => {
    mockSearch.mockResolvedValue(RESULTS)
    const user = userEvent.setup()
    renderBar()

    const input = getInput()
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
    const input = getInput()

    // A bare "k" must not hijack focus into the bar.
    fireEvent.keyDown(window, { key: 'k' })
    expect(input).not.toHaveFocus()

    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
    expect(input).toHaveFocus()
  })
})
