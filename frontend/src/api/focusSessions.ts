import { apiClient } from './client'
import type {
  FocusClock,
  FocusClockState,
  FocusFinishedBlock,
  FocusSessionLog,
} from '../types/focus'

/* The mobile Focus session on the server (#374): the day's finished blocks and
 * the one live clock. Every write is idempotent, so a retry or two writes
 * landing out of order settle on the same state. */

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export async function getFocusSessionLog(planDate: string): Promise<FocusSessionLog> {
  return apiClient<FocusSessionLog>(
    `/api/focus/sessions?plan_date=${encodeURIComponent(planDate)}`,
  )
}

/** Book a finished block; booking it again re-books it. Stops that block's own clock. */
export async function finishFocusBlock(
  planDate: string,
  taskId: number,
  minutes: number,
  plannedMinutes: number,
): Promise<FocusFinishedBlock> {
  return apiClient<FocusFinishedBlock>(
    `/api/focus/sessions/${encodeURIComponent(planDate)}/tasks/${taskId}`,
    {
      method: 'PUT',
      headers: JSON_HEADERS,
      body: JSON.stringify({ minutes, planned_minutes: plannedMinutes }),
    },
  )
}

/** Take a finished block back out — the undo of a completion. */
export async function removeFocusBlock(planDate: string, taskId: number): Promise<void> {
  await apiClient<void>(
    `/api/focus/sessions/${encodeURIComponent(planDate)}/tasks/${taskId}`,
    { method: 'DELETE' },
  )
}

export async function getFocusClock(): Promise<FocusClock | null> {
  return apiClient<FocusClock | null>('/api/focus/clock')
}

/**
 * Put the clock in a state. `elapsedMs` is the time already on the block; the
 * server stamps "now" itself, so device clocks never have to agree.
 */
export async function setFocusClock(
  planDate: string,
  taskId: number,
  state: FocusClockState,
  elapsedMs: number,
): Promise<FocusClock> {
  return apiClient<FocusClock>('/api/focus/clock', {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      plan_date: planDate,
      task_id: taskId,
      state,
      elapsed_ms: Math.max(0, Math.round(elapsedMs)),
    }),
  })
}

/** Clear the clock, but only if it is still this block's — another device may have moved on. */
export async function clearFocusClock(planDate: string, taskId: number): Promise<void> {
  const query = new URLSearchParams({ plan_date: planDate, task_id: String(taskId) })
  await apiClient<void>(`/api/focus/clock?${query.toString()}`, { method: 'DELETE' })
}
