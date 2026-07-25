import { useCallback, useEffect, useRef, useState } from 'react'
import { useToast } from '../../components/ToastContext'
import {
  createTask,
  createUnscopedTask,
  deleteTask,
  listAllTasks,
  listTasks,
  markTaskDone,
  skipOccurrence,
  updateTask,
} from '../../api/tasks'
import type { Task, TaskCreate, TaskUpdate } from '../../types/task'
import { useTrashCount } from '../trash/trashCountContext'
import { useTaskRefresh } from './taskRefreshContext'

interface UseTasks {
  tasks: Task[]
  /**
   * True during the initial load and whenever the project scope changes (no
   * data for the new scope yet); a same-scope background refetch uses
   * `refreshing` instead.
   */
  loading: boolean
  /** True while a same-scope reload/refetch is in flight after the first load. */
  refreshing: boolean
  error: string | null
  create: (data: TaskCreate) => Promise<void>
  update: (id: number, data: TaskUpdate) => Promise<void>
  markDone: (id: number) => Promise<void>
  skip: (id: number) => Promise<void>
  remove: (id: number) => Promise<void>
  reload: () => void
}

/** Tasks as loaded, tagged with the scope (project id, or undefined for all). */
interface LoadedTasks {
  scope: number | undefined
  tasks: Task[]
}

const NO_TASKS: Task[] = []

export function useTasks(projectId?: number): UseTasks {
  const [loaded, setLoaded] = useState<LoadedTasks | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const { withToast } = useToast()
  const { refresh: refreshTrashCount } = useTrashCount()
  // Sidebar drag-to-file changes tasks outside this hook — refetch off it too.
  const { version: taskRefreshVersion } = useTaskRefresh()
  // Mirrors `loaded` so the effect can tell a same-scope background refetch
  // (keep the list, flip `refreshing`) from a scope change (show `loading`).
  const loadedScope = useRef<LoadedTasks | null>(null)

  // Rows from another project must never be rendered — or mutated — under the
  // current scope, so anything loaded for a different scope is derived away.
  const tasks = loaded !== null && loaded.scope === projectId ? loaded.tasks : NO_TASKS

  const reload = useCallback(() => setRefreshKey((k) => k + 1), [])

  useEffect(() => {
    let active = true
    const sameScope = loadedScope.current !== null && loadedScope.current.scope === projectId
    if (sameScope) {
      setRefreshing(true)
    } else {
      // A new scope has no data yet: fall back to the initial-load treatment
      // rather than leaving the previous project's error on screen.
      setLoading(true)
      setError(null)
    }
    const request = projectId === undefined ? listAllTasks() : listTasks(projectId)
    request
      .then((data) => {
        if (!active) return
        const next = { scope: projectId, tasks: data }
        loadedScope.current = next
        setLoaded(next)
        setError(null)
      })
      .catch((e: unknown) => {
        if (active) {
          setError(e instanceof Error ? e.message : 'Failed to load tasks')
        }
      })
      .finally(() => {
        if (!active) return
        setLoading(false)
        setRefreshing(false)
      })
    return () => {
      active = false
    }
  }, [projectId, refreshKey, taskRefreshVersion])

  const create = useCallback(
    async (data: TaskCreate) => {
      // A payload carrying an explicit project (the task modal's selector) must
      // use the unscoped endpoint — the project-scoped route takes the project
      // from its path and ignores `data.project_id`.
      await withToast(
        projectId === undefined || data.project_id !== undefined
          ? createUnscopedTask(data)
          : createTask(projectId, data),
        { success: 'Task created' },
      )
      reload()
    },
    [projectId, reload, withToast],
  )

  const update = useCallback(
    async (id: number, data: TaskUpdate) => {
      await withToast(updateTask(id, data), { success: 'Task saved' })
      reload()
    },
    [reload, withToast],
  )

  const markDone = useCallback(
    async (id: number) => {
      await withToast(markTaskDone(id), { success: 'Task marked done' })
      reload()
    },
    [reload, withToast],
  )

  const skip = useCallback(
    async (id: number) => {
      // Skips the current occurrence (soft-deleted to trash) and advances the
      // series; refresh the trash count like a delete does.
      await withToast(skipOccurrence(id), { success: 'Occurrence skipped' })
      reload()
      void refreshTrashCount()
    },
    [reload, refreshTrashCount, withToast],
  )

  const remove = useCallback(
    async (id: number) => {
      await withToast(deleteTask(id), { success: 'Task moved to trash' })
      reload()
      void refreshTrashCount()
    },
    [reload, refreshTrashCount, withToast],
  )

  return { tasks, loading, refreshing, error, create, update, markDone, skip, remove, reload }
}
