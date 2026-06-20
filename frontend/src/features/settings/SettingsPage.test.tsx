import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  getEvalRuns,
  getProfiles,
  getPrompts,
  updateProfile,
} from '../../api/settings'
import type { Profile, Prompt } from '../../types/settings'
import { SettingsPage } from './SettingsPage'

vi.mock('../../api/settings', () => ({
  getProfiles: vi.fn(),
  getPrompts: vi.fn(),
  getEvalRuns: vi.fn(),
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
const mockUpdateProfile = vi.mocked(updateProfile)

function renderPage() {
  return render(
    <MemoryRouter>
      <SettingsPage />
    </MemoryRouter>,
  )
}

describe('SettingsPage edit safety', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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

    const modelInput = await screen.findByDisplayValue('gemma4:e2b')
    const saveButton = screen.getByRole('button', { name: 'Save' })

    // Pristine: nothing to save, no unsaved marker.
    expect(saveButton).toBeDisabled()
    expect(screen.queryByLabelText('Unsaved changes')).not.toBeInTheDocument()

    await user.clear(modelInput)
    await user.type(modelInput, 'llama3')

    expect(saveButton).toBeEnabled()
    expect(screen.getByLabelText('Unsaved changes')).toBeInTheDocument()
  })

  it('shows a Saved confirmation that auto-clears, and re-disables Save', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    mockUpdateProfile.mockResolvedValue({ ...profile, model: 'llama3' })

    try {
      renderPage()

      const modelInput = await screen.findByDisplayValue('gemma4:e2b')
      await user.clear(modelInput)
      await user.type(modelInput, 'llama3')

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
