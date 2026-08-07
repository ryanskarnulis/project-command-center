import { apiClient } from './client'
import type { FocusPlan } from '../types/focus'

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
