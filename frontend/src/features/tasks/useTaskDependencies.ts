import { useCallback, useEffect, useState } from 'react'
import {
  addDependency,
  listDependencies,
  removeDependency,
} from '../../api/taskDependencies'
import type { TaskDependency } from '../../types/task'

interface UseTaskDependencies {
  dependencies: TaskDependency[]
  loading: boolean
  error: string | null
  add: (dependsOnTaskId: number) => Promise<void>
  remove: (dependencyId: number) => Promise<void>
}

export function useTaskDependencies(taskId: number): UseTaskDependencies {
  const [dependencies, setDependencies] = useState<TaskDependency[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const reload = useCallback(() => setRefreshKey((k) => k + 1), [])

  useEffect(() => {
    let active = true
    listDependencies(taskId)
      .then((data) => {
        if (!active) return
        setDependencies(data)
        setError(null)
      })
      .catch((e: unknown) => {
        if (active) {
          setError(e instanceof Error ? e.message : 'Failed to load dependencies')
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [taskId, refreshKey])

  // Errors propagate to the caller so the form can show "would create a cycle".
  const add = useCallback(
    async (dependsOnTaskId: number) => {
      await addDependency(taskId, dependsOnTaskId)
      reload()
    },
    [taskId, reload],
  )

  const remove = useCallback(
    async (dependencyId: number) => {
      await removeDependency(taskId, dependencyId)
      reload()
    },
    [taskId, reload],
  )

  return { dependencies, loading, error, add, remove }
}
