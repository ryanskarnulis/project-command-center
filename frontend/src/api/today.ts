import { apiClient } from './client'
import type { TodayPlan } from '../types/today'

export interface TodayPlanParams {
  date?: string // YYYY-MM-DD
  startTime?: string // HH:MM
  availableMinutes?: number
}

/**
 * Fetch the deterministic day plan. Omitted params fall through to the backend
 * defaults (server today, 09:00, 360 minutes), so callers only send what they
 * actually want to override.
 */
export async function getTodayPlan(params: TodayPlanParams = {}): Promise<TodayPlan> {
  const query = new URLSearchParams()
  if (params.date) query.set('date', params.date)
  if (params.startTime) query.set('start_time', params.startTime)
  if (params.availableMinutes !== undefined) {
    query.set('available_minutes', String(params.availableMinutes))
  }
  const suffix = query.toString()
  const res = await apiClient(`/api/today${suffix ? `?${suffix}` : ''}`)
  return (await res.json()) as TodayPlan
}
