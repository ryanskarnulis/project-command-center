import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../../api/client'
import { restoreInbox } from '../../api/inbox'
import { restoreProject } from '../../api/projects'
import { restoreTask } from '../../api/tasks'
import { getTrash } from '../../api/trash'
import type { Trash } from '../../types/trash'

const EMPTY: Trash = { projects: [], tasks: [], inbox_items: [] }

interface UseTrash {
  trash: Trash
  loading: boolean
  error: string | null
  restoreProjectById: (id: number) => Promise<void>
  restoreTaskById: (id: number) => Promise<void>
  restoreInboxById: (id: number) => Promise<void>
}

export function useTrash(): UseTrash {
  const [trash, setTrash] = useState<Trash>(EMPTY)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const reload = useCallback(() => setRefreshKey((k) => k + 1), [])

  useEffect(() => {
    let active = true
    getTrash()
      .then((data) => {
        if (!active) return
        setTrash(data)
        setError(null)
      })
      .catch((e: unknown) => {
        if (active) {
          setError(e instanceof Error ? e.message : 'Failed to load trash')
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [refreshKey])

  const runRestore = useCallback(
    async (restore: () => Promise<unknown>) => {
      setError(null)
      try {
        await restore()
        reload()
      } catch (e: unknown) {
        // A dismissed inbox item can 409 if the same text was re-captured since.
        if (e instanceof ApiError && e.status === 409) {
          setError(
            'That note was re-captured after it was dismissed — the active copy already represents it.',
          )
          reload()
          return
        }
        setError(e instanceof Error ? e.message : 'Failed to restore item')
      }
    },
    [reload],
  )

  const restoreProjectById = useCallback(
    (id: number) => runRestore(() => restoreProject(id)),
    [runRestore],
  )
  const restoreTaskById = useCallback(
    (id: number) => runRestore(() => restoreTask(id)),
    [runRestore],
  )
  const restoreInboxById = useCallback(
    (id: number) => runRestore(() => restoreInbox(id)),
    [runRestore],
  )

  return {
    trash,
    loading,
    error,
    restoreProjectById,
    restoreTaskById,
    restoreInboxById,
  }
}
