import { apiClient } from './client'
import type { Task } from '../types/task'

export interface CalendarParams {
  start: string // YYYY-MM-DD, inclusive
  end: string // YYYY-MM-DD, inclusive
}

/**
 * Fetch accepted tasks due within `[start, end]` for the read-only calendar.
 * Returns a flat list; the caller buckets tasks onto day cells by `due_date`.
 */
export async function getCalendar({ start, end }: CalendarParams): Promise<Task[]> {
  const query = new URLSearchParams({ start, end })
  const res = await apiClient(`/api/calendar?${query.toString()}`)
  return (await res.json()) as Task[]
}
