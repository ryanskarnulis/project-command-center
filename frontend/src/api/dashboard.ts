import { apiClient } from './client'
import type { DashboardOverview } from '../types/dashboard'

export async function getDashboard(): Promise<DashboardOverview> {
  const res = await apiClient('/api/dashboard')
  return (await res.json()) as DashboardOverview
}
