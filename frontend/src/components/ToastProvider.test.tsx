import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, ApiTimeoutError } from '../api/client'
import { useToast } from './ToastContext'
import { ToastProvider } from './ToastProvider'

type Messages = { success: string; error?: string }
type Settled = (outcome: 'resolved' | 'rejected', value: unknown) => void

/** Runs one `withToast` call on click and reports how the wrapped promise settled. */
function Trigger({
  run,
  messages,
  onSettled,
}: {
  run: () => Promise<unknown>
  messages: Messages
  onSettled: Settled
}) {
  const { withToast } = useToast()
  return (
    <button
      type="button"
      onClick={() => {
        // `withToast` rethrows; catch it here or the rejection fails the test
        // as unhandled (see src/test/setup.ts).
        withToast(run(), messages).then(
          (value) => onSettled('resolved', value),
          (e: unknown) => onSettled('rejected', e),
        )
      }}
    >
      Go
    </button>
  )
}

function renderAndTrigger(run: () => Promise<unknown>, messages: Messages) {
  const onSettled = vi.fn<Settled>()
  render(
    <ToastProvider>
      <Trigger run={run} messages={messages} onSettled={onSettled} />
    </ToastProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'Go' }))
  return onSettled
}

describe('ToastProvider withToast', () => {
  it('toasts the success message and passes the value through', async () => {
    const onSettled = renderAndTrigger(() => Promise.resolve(42), {
      success: 'Task saved',
    })
    expect(await screen.findByRole('status')).toHaveTextContent('Task saved')
    await waitFor(() => expect(onSettled).toHaveBeenCalledWith('resolved', 42))
  })

  it('shows the server detail rather than "API error <status>"', async () => {
    const detail = 'This task is completed by its subtasks; complete them to complete it'
    const error = new ApiError(409, { detail })
    const onSettled = renderAndTrigger(() => Promise.reject(error), {
      success: 'Task marked done',
    })
    // The status number is something the user can do nothing with; the
    // backend's reason is what tells them what to do next.
    expect(await screen.findByRole('alert')).toHaveTextContent(detail)
    expect(screen.queryByText(/API error 409/)).not.toBeInTheDocument()
    // Rethrown unchanged, so callers keep their own handling.
    await waitFor(() => expect(onSettled).toHaveBeenCalledWith('rejected', error))
  })

  it('lets an explicit error override win over the detail', async () => {
    renderAndTrigger(() => Promise.reject(new ApiError(409, { detail: 'Server said no' })), {
      success: 'Saved',
      error: 'Could not save the task',
    })
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save the task')
    expect(screen.queryByText('Server said no')).not.toBeInTheDocument()
  })

  it('keeps the status message when the error body has no usable detail', async () => {
    renderAndTrigger(() => Promise.reject(new ApiError(500, null)), { success: 'Saved' })
    expect(await screen.findByRole('alert')).toHaveTextContent('API error 500')
  })

  it('keeps a non-API error’s own wording', async () => {
    renderAndTrigger(() => Promise.reject(new ApiTimeoutError(30_000)), { success: 'Saved' })
    expect(await screen.findByRole('alert')).toHaveTextContent(/timed out after 30s/)
  })

  it('falls back to a generic line for a non-Error rejection', async () => {
    renderAndTrigger(() => Promise.reject('boom'), { success: 'Saved' })
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
  })

  it('removes a toast when its dismiss button is clicked', async () => {
    renderAndTrigger(() => Promise.resolve(undefined), { success: 'Task saved' })
    const toast = await screen.findByRole('status')
    fireEvent.click(within(toast).getByRole('button', { name: 'Dismiss notification' }))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})
