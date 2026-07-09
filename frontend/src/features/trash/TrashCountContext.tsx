import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { getTrashCount } from '../../api/trash'
import {
  TrashCountContext,
  ZERO_TRASH_COUNTS,
  type TrashCounts,
} from './trashCountContext'

function toTrashCounts(counts: TrashCounts): TrashCounts {
  return {
    projects: counts.projects,
    tasks: counts.tasks,
  }
}

/**
 * App-wide trash counts. Fetches once on mount and re-reads on demand. `useTrash`
 * calls `refresh()` after any restore/purge so the badge and section headings stay
 * live. Errors are swallowed — these are best-effort and must never break the shell.
 */
export function TrashCountProvider({ children }: { children: ReactNode }) {
  const [counts, setCounts] = useState<TrashCounts>(ZERO_TRASH_COUNTS)

  const refresh = useCallback(async () => {
    try {
      const next = await getTrashCount()
      setCounts(toTrashCounts(next))
    } catch {
      // best-effort; leave the prior counts in place
    }
  }, [])

  useEffect(() => {
    let active = true
    getTrashCount()
      .then((next) => {
        if (active) setCounts(toTrashCounts(next))
      })
      .catch(() => {
        // best-effort; leave the prior counts in place
      })
    return () => {
      active = false
    }
  }, [])

  const count = counts.projects + counts.tasks

  return (
    <TrashCountContext.Provider value={{ count, counts, refresh }}>
      {children}
    </TrashCountContext.Provider>
  )
}
