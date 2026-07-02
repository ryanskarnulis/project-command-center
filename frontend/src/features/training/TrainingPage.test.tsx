import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  deleteTrainingExample,
  getTrainingStats,
  listTrainingExamples,
} from '../../api/training'
import type { TrainingExample, TrainingStats } from '../../types/training'
import { TrainingPage } from './TrainingPage'

// PAGE_SIZE is mocked small so the pagination path is testable without 50 rows.
vi.mock('../../api/training', () => ({
  getTrainingStats: vi.fn(),
  listTrainingExamples: vi.fn(),
  deleteTrainingExample: vi.fn(),
  PAGE_SIZE: 2,
}))

const mockGetStats = vi.mocked(getTrainingStats)
const mockListExamples = vi.mocked(listTrainingExamples)
const mockDeleteExample = vi.mocked(deleteTrainingExample)

const CREATED_AT = new Date(Date.now() - 3 * 86_400_000).toISOString()

function example(overrides: Partial<TrainingExample>): TrainingExample {
  return {
    id: 1,
    task_name: 'task_extraction',
    input_text: 'messy note',
    model_output_json: '{"tasks": []}',
    corrected_output_json: null,
    accepted: false,
    model_profile: 'default',
    model_name: 'gemma4:e2b',
    created_at: CREATED_AT,
    deleted_at: null,
    ...overrides,
  }
}

const stats: TrainingStats = {
  total: 12,
  accepted: 9,
  by_task: { task_extraction: { count: 12, accepted: 9 } },
  profiles: ['default'],
  goal: 200,
  remaining: 188,
}

describe('TrainingPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetStats.mockResolvedValue(stats)
    mockListExamples.mockResolvedValue([
      example({ id: 1, accepted: true }),
      example({
        id: 2,
        accepted: true,
        corrected_output_json: '{"tasks": [{"title": "fixed"}]}',
      }),
    ])
  })

  it('renders corpus stats and one row per example with its status', async () => {
    render(<TrainingPage />)

    const progress = await screen.findByText(/to go before fine-tuning is viable/)
    expect(progress.textContent).toContain('12')
    expect(progress.textContent).toContain('188 to go')
    // Row 1 is plain-accepted; row 2 carries a correction, which wins.
    expect(
      screen.getAllByText('accepted', { selector: '.status-pill' }).length,
    ).toBeGreaterThan(0)
    expect(
      screen.getByText('corrected', { selector: '.status-pill' }),
    ).toBeInTheDocument()
  })

  it('labels an unaccepted, uncorrected example as an extraction failure', async () => {
    mockListExamples.mockResolvedValue([example({ id: 3, accepted: false })])
    render(<TrainingPage />)

    expect(
      await screen.findByText('extraction failure', { selector: '.status-pill' }),
    ).toBeInTheDocument()
  })

  it('shows the empty state when the corpus is empty', async () => {
    mockListExamples.mockResolvedValue([])
    render(<TrainingPage />)

    expect(
      await screen.findByText(/No training examples yet/),
    ).toBeInTheDocument()
  })

  it('passes the status filter through to the API', async () => {
    render(<TrainingPage />)
    await screen.findByText('corrected', { selector: '.status-pill' })

    await userEvent.selectOptions(
      screen.getByLabelText('Filter by status'),
      'corrected',
    )

    await waitFor(() =>
      expect(mockListExamples).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: 'corrected' }),
        2,
        0,
      ),
    )
  })

  it('loads the next page from the current offset', async () => {
    render(<TrainingPage />)
    await screen.findByText('corrected', { selector: '.status-pill' })
    mockListExamples.mockResolvedValue([example({ id: 3 })])

    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))

    await waitFor(() => expect(mockListExamples).toHaveBeenLastCalledWith({}, 2, 2))
    // A short page means the end was reached — the button goes away.
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Load more' }),
      ).not.toBeInTheDocument(),
    )
  })

  it('moves an example to trash after confirmation and drops the row', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockDeleteExample.mockResolvedValue(undefined)
    render(<TrainingPage />)
    await screen.findByText('corrected', { selector: '.status-pill' })

    const deleteButtons = screen.getAllByRole('button', {
      name: /Move task_extraction example to trash/,
    })
    await userEvent.click(deleteButtons[0])

    expect(confirmSpy).toHaveBeenCalled()
    await waitFor(() => expect(mockDeleteExample).toHaveBeenCalledWith(1))
    await waitFor(() =>
      expect(
        screen.getAllByRole('button', {
          name: /Move task_extraction example to trash/,
        }),
      ).toHaveLength(1),
    )
    confirmSpy.mockRestore()
  })

  it('does not delete when the confirmation is declined', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<TrainingPage />)
    await screen.findByText('corrected', { selector: '.status-pill' })

    await userEvent.click(
      screen.getAllByRole('button', {
        name: /Move task_extraction example to trash/,
      })[0],
    )

    expect(mockDeleteExample).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })
})
