import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  getEvalRuns,
  getModels,
  getOllamaStatus,
  getProfiles,
  getPrompts,
  runEval,
  updateProfile,
} from '../../api/settings'
import type { EvalRunRecord, Profile, Prompt } from '../../types/settings'
import { SettingsPage } from './SettingsPage'

vi.mock('../../api/settings', () => ({
  getProfiles: vi.fn(),
  getPrompts: vi.fn(),
  getEvalRuns: vi.fn(),
  getOllamaStatus: vi.fn(),
  getModels: vi.fn(),
  updateProfile: vi.fn(),
  putPrompt: vi.fn(),
  runEval: vi.fn(),
}))

const profile: Profile = {
  name: 'task_extraction',
  provider: 'ollama',
  model: 'gemma4:e2b',
  temperature: 0.2,
  max_tokens: 1024,
  response_mode: 'json_schema',
  system_prompt: 'extract_tasks.md',
  overridden_fields: [],
}

const prompts: Prompt[] = [
  { name: 'extract_tasks.md', text: 'Extract tasks from the input.' },
]

const mockGetProfiles = vi.mocked(getProfiles)
const mockGetPrompts = vi.mocked(getPrompts)
const mockGetEvalRuns = vi.mocked(getEvalRuns)
const mockGetOllamaStatus = vi.mocked(getOllamaStatus)
const mockGetModels = vi.mocked(getModels)
const mockUpdateProfile = vi.mocked(updateProfile)
const mockRunEval = vi.mocked(runEval)

// Health/model introspection is best-effort and orthogonal to these tests;
// default it to "reachable, with llama3 also installed" so the model dropdown
// has another option to switch to (the profile's gemma4:e2b is added on top).
function mockOllamaDefaults() {
  mockGetOllamaStatus.mockResolvedValue({ reachable: true, host: 'http://localhost:11434' })
  mockGetModels.mockResolvedValue(['gemma4:e2b', 'llama3'])
}

function renderPage() {
  const router = createMemoryRouter(
    [{ path: '/settings', element: <SettingsPage /> }],
    { initialEntries: ['/settings'] },
  )
  return render(<RouterProvider router={router} />)
}

function renderPageWithNav() {
  const router = createMemoryRouter(
    [
      {
        path: '/settings',
        element: (
          <>
            <SettingsPage />
            <Link to="/dashboard">Dashboard</Link>
          </>
        ),
      },
      { path: '/dashboard', element: <main>Dashboard target</main> },
    ],
    { initialEntries: ['/settings'] },
  )
  return render(<RouterProvider router={router} />)
}

describe('SettingsPage edit safety', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockOllamaDefaults()
    mockGetProfiles.mockResolvedValue([profile])
    mockGetPrompts.mockResolvedValue(prompts)
    mockGetEvalRuns.mockResolvedValue([])
  })

  afterEach(() => {
    cleanup()
  })

  it('gates Save on real changes and shows an unsaved indicator', async () => {
    const user = userEvent.setup()
    renderPage()

    const modelSelect = await screen.findByDisplayValue('gemma4:e2b')
    const saveButton = screen.getByRole('button', { name: 'Save' })

    // Pristine: nothing to save, no unsaved marker.
    expect(saveButton).toBeDisabled()
    expect(screen.queryByLabelText('Unsaved changes')).not.toBeInTheDocument()

    await user.selectOptions(modelSelect, 'llama3')

    expect(saveButton).toBeEnabled()
    expect(screen.getByLabelText('Unsaved changes')).toBeInTheDocument()
  })

  it('blocks in-app navigation while settings are dirty and can stay', async () => {
    const user = userEvent.setup()
    renderPageWithNav()

    const modelSelect = await screen.findByDisplayValue('gemma4:e2b')
    await user.selectOptions(modelSelect, 'llama3')
    await user.click(screen.getByRole('link', { name: 'Dashboard' }))

    expect(
      screen.getByRole('dialog', { name: 'Discard unsaved settings?' }),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Stay' }))

    expect(screen.queryByRole('dialog', { name: 'Discard unsaved settings?' })).not.toBeInTheDocument()
    expect(screen.getByText('Settings')).toBeInTheDocument()
    expect(screen.queryByText('Dashboard target')).not.toBeInTheDocument()
  })

  it('allows in-app navigation after confirming unsaved settings should be discarded', async () => {
    const user = userEvent.setup()
    renderPageWithNav()

    const modelSelect = await screen.findByDisplayValue('gemma4:e2b')
    await user.selectOptions(modelSelect, 'llama3')
    await user.click(screen.getByRole('link', { name: 'Dashboard' }))
    await user.click(screen.getByRole('button', { name: 'Leave without saving' }))

    expect(await screen.findByText('Dashboard target')).toBeInTheDocument()
  })

  it('attaches the browser close/reload guard only while settings are dirty', async () => {
    const user = userEvent.setup()
    const addSpy = vi.spyOn(window, 'addEventListener')
    const removeSpy = vi.spyOn(window, 'removeEventListener')

    renderPage()

    const modelSelect = await screen.findByDisplayValue('gemma4:e2b')
    expect(addSpy).not.toHaveBeenCalledWith('beforeunload', expect.any(Function))

    await user.selectOptions(modelSelect, 'llama3')

    await waitFor(() =>
      expect(addSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function)),
    )

    cleanup()
    expect(removeSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function))
    addSpy.mockRestore()
    removeSpy.mockRestore()
  })

  it('shows a Saved confirmation that auto-clears, and re-disables Save', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    mockUpdateProfile.mockResolvedValue({ ...profile, model: 'llama3' })

    try {
      renderPage()

      const modelSelect = await screen.findByDisplayValue('gemma4:e2b')
      await user.selectOptions(modelSelect, 'llama3')

      await user.click(screen.getByRole('button', { name: 'Save' }))

      expect(await screen.findByText('Saved')).toBeInTheDocument()
      expect(mockUpdateProfile).toHaveBeenCalledWith('task_extraction', {
        model: 'llama3',
      })
      // Save committed: no longer dirty, button disabled again.
      expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
      expect(screen.queryByLabelText('Unsaved changes')).not.toBeInTheDocument()

      // Confirmation auto-clears after the timeout.
      act(() => {
        vi.advanceTimersByTime(3000)
      })
      await waitFor(() =>
        expect(screen.queryByText('Saved')).not.toBeInTheDocument(),
      )
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('SettingsPage prompt editor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockOllamaDefaults()
    mockGetProfiles.mockResolvedValue([profile])
    mockGetPrompts.mockResolvedValue(prompts)
    mockGetEvalRuns.mockResolvedValue([])
  })

  afterEach(() => {
    cleanup()
  })

  it('shows the consuming workflow tag and a live character count', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Prompts/ }))

    const textarea = await screen.findByDisplayValue('Extract tasks from the input.')
    // The profile's system_prompt (extract_tasks.md) ties it to this prompt.
    expect(screen.getByText('task_extraction')).toBeInTheDocument()
    expect(screen.getByText('29 chars')).toBeInTheDocument()

    await user.type(textarea, '!')
    expect(screen.getByText('30 chars')).toBeInTheDocument()
  })

  it('reverts edits to the last-saved text and re-disables Save/Revert', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Prompts/ }))

    const textarea = await screen.findByDisplayValue('Extract tasks from the input.')
    const revert = screen.getByRole('button', { name: 'Revert' })

    // Pristine: nothing to revert or save.
    expect(revert).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()

    await user.type(textarea, ' more text')
    expect(revert).toBeEnabled()
    expect(screen.getByLabelText('Unsaved changes')).toBeInTheDocument()

    await user.click(revert)
    expect(screen.getByDisplayValue('Extract tasks from the input.')).toBeInTheDocument()
    expect(revert).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(screen.queryByLabelText('Unsaved changes')).not.toBeInTheDocument()
  })
})

describe('SettingsPage eval trend', () => {
  // Newest-first, as the API returns them. 1/4 then 4/4 → latest is 100%.
  const runs: EvalRunRecord[] = [
    {
      id: 2,
      suite: 'task_extraction',
      passed: 4,
      total: 4,
      created_at: '2026-06-19T10:00:00Z',
    },
    {
      id: 1,
      suite: 'task_extraction',
      passed: 1,
      total: 4,
      created_at: '2026-06-18T10:00:00Z',
    },
  ]

  beforeEach(() => {
    vi.clearAllMocks()
    mockOllamaDefaults()
    mockGetProfiles.mockResolvedValue([profile])
    mockGetPrompts.mockResolvedValue(prompts)
  })

  afterEach(() => {
    cleanup()
  })

  it('renders a pass-rate trend from loaded runs and an empty state otherwise', async () => {
    const user = userEvent.setup()
    mockGetEvalRuns.mockResolvedValue(runs)
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Evals/ }))

    // Latest run is 4/4 across 2 runs for task_extraction.
    expect(await screen.findByText('100% · 2 runs')).toBeInTheDocument()
    // The other two suites have no history.
    expect(screen.getAllByText('No runs yet')).toHaveLength(2)
  })

  it('runs every suite in sequence when "Run all suites" is clicked', async () => {
    const user = userEvent.setup()
    mockGetEvalRuns.mockResolvedValue([])
    mockRunEval.mockImplementation(async (suite: string) => ({
      suite,
      passed: 1,
      total: 1,
      cases: [],
    }))
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Evals/ }))
    await user.click(screen.getByRole('button', { name: 'Run all suites' }))

    await waitFor(() => expect(mockRunEval).toHaveBeenCalledTimes(3))
    expect(mockRunEval).toHaveBeenCalledWith('task_extraction')
    expect(mockRunEval).toHaveBeenCalledWith('project_matching')
    expect(mockRunEval).toHaveBeenCalledWith('summary')
  })
})
