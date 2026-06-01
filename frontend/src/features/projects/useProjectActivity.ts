import { useEffect, useState } from 'react'
import { getProjectActivity } from '../../api/projects'
import type { ActivityEvent } from '../../types/project'

interface UseProjectActivity {
  events: ActivityEvent[]
  loading: boolean
  error: string | null
}

/**
 * Recent activity for one project. Re-fetches whenever `refreshKey` changes, so
 * the Tasks page can bump it after a task mutation to keep the feed in sync.
 */
export function useProjectActivity(
  projectId: number,
  refreshKey = 0,
): UseProjectActivity {
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    getProjectActivity(projectId)
      .then((data) => {
        if (!active) return
        setEvents(data)
        setError(null)
      })
      .catch((e: unknown) => {
        if (active) {
          setError(e instanceof Error ? e.message : 'Failed to load activity')
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [projectId, refreshKey])

  return { events, loading, error }
}
