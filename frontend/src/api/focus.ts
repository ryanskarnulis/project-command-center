import { apiClient } from './client'
import type { FocusEntry, FocusEntryUpdate, FocusPlan } from '../types/focus'
import type { RepeatInterval } from '../types/task'

export interface FocusPlanParams {
  date?: string // YYYY-MM-DD
  startTime?: string // HH:MM
  availableMinutes?: number
}

/**
 * Fetch the deterministic day plan. Omitted params fall through to the backend
 * defaults (server today, 09:00, 360 minutes), so callers only send what they
 * actually want to override.
 */
export async function getFocusPlan(params: FocusPlanParams = {}): Promise<FocusPlan> {
  const query = new URLSearchParams()
  if (params.date) query.set('date', params.date)
  if (params.startTime) query.set('start_time', params.startTime)
  if (params.availableMinutes !== undefined) {
    query.set('available_minutes', String(params.availableMinutes))
  }
  const suffix = query.toString()
  return apiClient<FocusPlan>(`/api/focus${suffix ? `?${suffix}` : ''}`)
}

// --- The stored day plan (#319) ---------------------------------------------

const JSON_HEADERS = { 'Content-Type': 'application/json' }

/**
 * Pin a task into a day. Without `startTime` it joins the day ahead of the
 * ranked work and never lands in "Didn't fit"; with one it is a fixed block.
 * Re-pinning a pinned task moves it.
 */
export async function pinFocusTask(
  planDate: string,
  taskId: number,
  startTime?: string,
): Promise<FocusEntry> {
  return apiClient<FocusEntry>('/api/focus/pins', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      plan_date: planDate,
      task_id: taskId,
      ...(startTime ? { start_time: startTime } : {}),
    }),
  })
}

/**
 * Reorder a day's floating pins, first to last (#375). The named pins trade
 * the slots they hold; pins left out (including ones the plan is ignoring)
 * keep their place. Returns the day's pins in their new order.
 */
export async function reorderFocusPins(
  planDate: string,
  entryIds: number[],
): Promise<FocusEntry[]> {
  return apiClient<FocusEntry[]>('/api/focus/pins/order', {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify({ plan_date: planDate, entry_ids: entryIds }),
  })
}

/**
 * Block fixed time for something that is not a task (a meeting). With
 * `repeatInterval` it repeats from `planDate` on (#376).
 */
export async function addFocusCommitment(
  planDate: string,
  title: string,
  startTime: string,
  durationMinutes: number,
  repeatInterval: RepeatInterval | null = null,
): Promise<FocusEntry> {
  return apiClient<FocusEntry>('/api/focus/commitments', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      plan_date: planDate,
      title,
      start_time: startTime,
      duration_minutes: durationMinutes,
      ...(repeatInterval ? { repeat_interval: repeatInterval } : {}),
    }),
  })
}

/**
 * Skip one day's occurrence of a repeating commitment (#376). Returns the
 * skip; `removeFocusEntry(skip.id)` undoes it.
 */
export async function skipFocusOccurrence(
  seriesEntryId: number,
  planDate: string,
): Promise<FocusEntry> {
  return apiClient<FocusEntry>(`/api/focus/entries/${seriesEntryId}/skip`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ plan_date: planDate }),
  })
}

export async function updateFocusEntry(
  entryId: number,
  data: FocusEntryUpdate,
): Promise<FocusEntry> {
  return apiClient<FocusEntry>(`/api/focus/entries/${entryId}`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify(data),
  })
}

/** Unpin a task or drop a commitment (a soft delete; `restoreFocusEntry` undoes it). */
export async function removeFocusEntry(entryId: number): Promise<FocusEntry> {
  return apiClient<FocusEntry>(`/api/focus/entries/${entryId}`, { method: 'DELETE' })
}

export async function restoreFocusEntry(entryId: number): Promise<FocusEntry> {
  return apiClient<FocusEntry>(`/api/focus/entries/${entryId}/restore`, {
    method: 'POST',
  })
}
