import { apiClient } from './client'
import type { FocusEntry, FocusEntryUpdate, FocusPlan } from '../types/focus'

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

/** Block fixed time for something that is not a task (a meeting). */
export async function addFocusCommitment(
  planDate: string,
  title: string,
  startTime: string,
  durationMinutes: number,
): Promise<FocusEntry> {
  return apiClient<FocusEntry>('/api/focus/commitments', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      plan_date: planDate,
      title,
      start_time: startTime,
      duration_minutes: durationMinutes,
    }),
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
