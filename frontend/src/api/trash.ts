import { apiClient } from './client'
import type { Trash } from '../types/trash'

export async function getTrash(): Promise<Trash> {
  const res = await apiClient('/api/trash')
  return (await res.json()) as Trash
}
