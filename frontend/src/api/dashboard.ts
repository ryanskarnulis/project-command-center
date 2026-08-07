import { apiClient } from './client'
import type { DashboardOverview } from '../types/dashboard'

export async function getDashboard(): Promise<DashboardOverview> {
  return apiClient<DashboardOverview>('/api/dashboard')
}
