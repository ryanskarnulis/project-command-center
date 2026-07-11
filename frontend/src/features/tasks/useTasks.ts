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
  /** True only during the initial load; a background refetch uses `refreshing`. */
  loading: boolean
  /** True while a reload/cross-page refetch is in flight after the first load. */
  refreshing: boolean
  error: string | null
  create: (data: TaskCreate) => Promise<void>
  update: (id: number, data: TaskUpdate) => Promise<void>
  markDone: (id: number) => Promise<void>
  skip: (id: number) => Promise<void>
  remove: (id: number) => Promise<void>
  reload: () => void
}

export function useTasks(projectId?: number): UseTasks {
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const { withToast } = useToast()
  const { refresh: refreshTrashCount } = useTrashCount()
  // Sidebar drag-to-file changes tasks outside this hook — refetch off it too.
  const { version: taskRefreshVersion } = useTaskRefresh()
  // Once the first load resolves we never flip `loading` back on — subsequent
  // refetches surface through `refreshing` so the list doesn't flash a spinner.
  const hasLoaded = useRef(false)

  const reload = useCallback(() => setRefreshKey((k) => k + 1), [])

  useEffect(() => {
    let active = true
    if (hasLoaded.current) setRefreshing(true)
    const request = projectId === undefined ? listAllTasks() : listTasks(projectId)
    request
      .then((data) => {
        if (!active) return
        setTasks(data)
        setError(null)
      })
      .catch((e: unknown) => {
        if (active) {
          setError(e instanceof Error ? e.message : 'Failed to load tasks')
        }
      })
      .finally(() => {
        if (!active) return
        hasLoaded.current = true
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
