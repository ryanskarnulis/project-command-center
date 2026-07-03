import { useMemo, useState, type ReactNode } from 'react'
import { TaskRefreshContext } from './taskRefreshContext'

/**
 * Shared "tasks changed elsewhere" signal. The sidebar drag-to-file action
 * lives outside every page's data hooks, so it bumps this version and the
 * task-loading hooks refetch off it.
 */
export function TaskRefreshProvider({ children }: { children: ReactNode }) {
  const [version, setVersion] = useState(0)
  const value = useMemo(
    () => ({ version, bump: () => setVersion((v) => v + 1) }),
    [version],
  )
  return (
    <TaskRefreshContext.Provider value={value}>
      {children}
    </TaskRefreshContext.Provider>
  )
}
