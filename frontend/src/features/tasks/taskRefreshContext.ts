import { createContext, useContext } from 'react'

export interface TaskRefresh {
  /** Bumped when tasks change outside a page's own hooks (e.g. sidebar drop). */
  version: number
  bump: () => void
}

// No-op default so components render fine without a provider (mirrors
// TrashCountContext) — pages simply don't cross-refresh.
export const TaskRefreshContext = createContext<TaskRefresh>({
  version: 0,
  bump: () => {},
})

export function useTaskRefresh(): TaskRefresh {
  return useContext(TaskRefreshContext)
}
