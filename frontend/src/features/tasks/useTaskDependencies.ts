import { useCallback, useEffect, useState } from 'react'
import {
  addDependency,
  listDependencies,
  listDependents,
  removeDependency,
} from '../../api/taskDependencies'
import type { TaskDependency, TaskDependent } from '../../types/task'

interface UseTaskDependencies {
  dependencies: TaskDependency[]
  dependents: TaskDependent[]
  loading: boolean
  error: string | null
  add: (dependsOnTaskId: number) => Promise<void>
  remove: (dependencyId: number) => Promise<void>
}

export function useTaskDependencies(taskId: number): UseTaskDependencies {
  const [dependencies, setDependencies] = useState<TaskDependency[]>([])
  const [dependents, setDependents] = useState<TaskDependent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const reload = useCallback(() => {
    setLoading(true)
    setRefreshKey((k) => k + 1)
  }, [])

  useEffect(() => {
    let active = true
    Promise.all([listDependencies(taskId), listDependents(taskId)])
      .then(([dependencies, dependents]) => {
        if (!active) return
        setDependencies(dependencies)
        setDependents(dependents)
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

  // Errors from `add`/`remove` propagate to the caller so the UI can show the
  // API's reason ("would create a cycle"). Neither is safe to fire-and-forget:
  // the caller must catch and surface, or the failure is invisible. Only a
  // successful mutation reloads the list.
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

  return { dependencies, dependents, loading, error, add, remove }
}
