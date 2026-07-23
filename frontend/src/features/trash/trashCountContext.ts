import { createContext, useContext } from 'react'

export interface TrashCounts {
  projects: number
  tasks: number
  /** Exact rows Empty-trash removes (incl. cascade tasks, excl. protected projects). */
  purge_total: number
}

export interface TrashCount {
  // Sum across kinds, for the topbar badge.
  count: number
  // Exact per-kind totals (unbounded by the /trash list page), for the section
  // headings on the trash page.
  counts: TrashCounts
  refresh: () => Promise<void>
}

export const ZERO_TRASH_COUNTS: TrashCounts = {
  projects: 0,
  tasks: 0,
  purge_total: 0,
}

// Default is a no-op so consumers (e.g. AppShell) render fine with no provider —
// the badge simply hides at 0 and headings fall back to their loaded lengths.
export const TrashCountContext = createContext<TrashCount>({
  count: 0,
  counts: ZERO_TRASH_COUNTS,
  refresh: async () => {},
})

export function useTrashCount(): TrashCount {
  return useContext(TrashCountContext)
}
