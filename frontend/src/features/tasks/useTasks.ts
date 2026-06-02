import { useCallback, useEffect, useState } from 'react'
import {
  createTask,
  createUnscopedTask,
  deleteTask,
  listAllTasks,
  listTasks,
  markTaskDone,
  updateTask,
} from '../../api/tasks'
import type { Task, TaskCreate, TaskUpdate } from '../../types/task'

interface UseTasks {
  tasks: Task[]
  loading: boolean
  error: string | null
  create: (data: TaskCreate) => Promise<void>
  update: (id: number, data: TaskUpdate) => Promise<void>
  markDone: (id: number) => Promise<void>
  remove: (id: number) => Promise<void>
  reload: () => void
}

export function useTasks(projectId?: number): UseTasks {
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const reload = useCallback(() => setRefreshKey((k) => k + 1), [])

  useEffect(() => {
    let active = true
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
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [projectId, refreshKey])

  const create = useCallback(
    async (data: TaskCreate) => {
      if (projectId === undefined) {
        await createUnscopedTask(data)
      } else {
        await createTask(projectId, data)
      }
      reload()
    },
    [projectId, reload],
  )

  const update = useCallback(
    async (id: number, data: TaskUpdate) => {
      await updateTask(id, data)
      reload()
    },
    [reload],
  )

  const markDone = useCallback(
    async (id: number) => {
      await markTaskDone(id)
      reload()
    },
    [reload],
  )

  const remove = useCallback(
    async (id: number) => {
      await deleteTask(id)
      reload()
    },
    [reload],
  )

  return { tasks, loading, error, create, update, markDone, remove, reload }
}
