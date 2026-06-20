import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { getTrashCount } from '../../api/trash'

interface TrashCounts {
  projects: number
  tasks: number
  inbox_items: number
}

interface TrashCount {
  // Sum across kinds, for the sidebar badge.
  count: number
  // Exact per-kind totals (unbounded by the /trash list page), for the section
  // headings on the trash page.
  counts: TrashCounts
  refresh: () => Promise<void>
}

const ZERO: TrashCounts = { projects: 0, tasks: 0, inbox_items: 0 }

// Default is a no-op so consumers (e.g. AppShell) render fine with no provider —
// the badge simply hides at 0 and headings fall back to their loaded lengths.
const TrashCountContext = createContext<TrashCount>({
  count: 0,
  counts: ZERO,
  refresh: async () => {},
})

/**
 * App-wide trash counts. Fetches once on mount and re-reads on demand. `useTrash`
 * calls `refresh()` after any restore/purge so the badge and section headings stay
 * live. Errors are swallowed — these are best-effort and must never break the shell.
 */
export function TrashCountProvider({ children }: { children: ReactNode }) {
  const [counts, setCounts] = useState<TrashCounts>(ZERO)

  const refresh = useCallback(async () => {
    try {
      const next = await getTrashCount()
      setCounts({
        projects: next.projects,
        tasks: next.tasks,
        inbox_items: next.inbox_items,
      })
    } catch {
      // best-effort; leave the prior counts in place
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const count = counts.projects + counts.tasks + counts.inbox_items

  return (
    <TrashCountContext.Provider value={{ count, counts, refresh }}>
      {children}
    </TrashCountContext.Provider>
  )
}

export function useTrashCount(): TrashCount {
  return useContext(TrashCountContext)
}
