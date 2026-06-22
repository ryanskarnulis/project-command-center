import { useEffect, useState } from 'react'
import { ApiError } from '../../api/client'
import { getProjectGantt } from '../../api/planning'
import type { ProjectGantt } from '../../types/planning'

interface UseProjectGantt {
  data: ProjectGantt | null
  loading: boolean
  error: string | null
}

/**
 * Fetch the read-only planning payload for a project, with the shared
 * loading/error baseline. Refetches when the project id changes.
 */
export function useProjectGantt(projectId: number): UseProjectGantt {
  const [data, setData] = useState<ProjectGantt | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadedId, setLoadedId] = useState<number | null>(null)

  useEffect(() => {
    let active = true
    getProjectGantt(projectId)
      .then((result) => {
        if (!active) return
        setData(result)
        setError(null)
        setLoadedId(projectId)
      })
      .catch((err: unknown) => {
        if (!active) return
        const message =
          err instanceof ApiError
            ? ((err.body as { detail?: string })?.detail ?? `Error ${err.status}`)
            : err instanceof Error
              ? err.message
              : 'Failed to load the timeline'
        setError(message)
        setLoadedId(projectId)
      })
    return () => {
      active = false
    }
  }, [projectId])

  return { data, loading: loadedId !== projectId, error }
}
