import { useEffect, useState } from 'react'
import { getProjectActivity } from '../../api/projects'
import type { ActivityEvent } from '../../types/project'

interface UseProjectActivity {
  events: ActivityEvent[]
  loading: boolean
  error: string | null
}

/** Activity as loaded, tagged with the project that produced it. */
interface LoadedActivity {
  scope: number
  events: ActivityEvent[]
}

/** An error tagged with the project whose request produced it. */
interface ScopedError {
  scope: number
  message: string
}

const NO_EVENTS: ActivityEvent[] = []

/**
 * Recent activity for one project. Re-fetches whenever `refreshKey` changes, so
 * the Tasks page can bump it after a task mutation to keep the feed in sync.
 */
export function useProjectActivity(
  projectId: number,
  refreshKey = 0,
): UseProjectActivity {
  const [loaded, setLoaded] = useState<LoadedActivity | null>(null)
  const [scopedError, setScopedError] = useState<ScopedError | null>(null)

  // One project's audit history must never render under another's heading, so
  // anything held for a different scope is derived away instead of shown.
  const events = loaded !== null && loaded.scope === projectId ? loaded.events : NO_EVENTS
  const error =
    scopedError !== null && scopedError.scope === projectId ? scopedError.message : null

  // Loading describes the project on screen, not the hook's lifetime: true
  // until this project's request settles. A `refreshKey` bump re-fetches a
  // scope that is already settled, so the feed keeps its rows while refreshing.
  const loading = (loaded === null || loaded.scope !== projectId) && error === null

  useEffect(() => {
    let active = true
    getProjectActivity(projectId)
      .then((data) => {
        if (!active) return
        setLoaded({ scope: projectId, events: data })
        setScopedError(null)
      })
      .catch((e: unknown) => {
        if (!active) return
        setScopedError({
          scope: projectId,
          message: e instanceof Error ? e.message : 'Failed to load activity',
        })
      })
    return () => {
      active = false
    }
  }, [projectId, refreshKey])

  return { events, loading, error }
}
