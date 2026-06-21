import { useCallback, useEffect, useState } from 'react'
import { useToast } from '../../components/ToastContext'
import { createProject, deleteProject, listProjects, updateProject } from '../../api/projects'
import type { Project, ProjectCreate, ProjectUpdate } from '../../types/project'

interface UseProjects {
  projects: Project[]
  loading: boolean
  error: string | null
  create: (data: ProjectCreate) => Promise<void>
  update: (id: number, data: ProjectUpdate) => Promise<void>
  remove: (id: number) => Promise<void>
  reload: () => void
}

export function useProjects(): UseProjects {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const { withToast } = useToast()

  const reload = useCallback(() => setRefreshKey((k) => k + 1), [])

  useEffect(() => {
    let active = true
    listProjects()
      .then((data) => {
        if (!active) return
        setProjects(data)
        setError(null)
      })
      .catch((e: unknown) => {
        if (active) {
          setError(e instanceof Error ? e.message : 'Failed to load projects')
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [refreshKey])

  const create = useCallback(
    async (data: ProjectCreate) => {
      await withToast(createProject(data), { success: 'Project created' })
      reload()
    },
    [reload, withToast],
  )

  const update = useCallback(
    async (id: number, data: ProjectUpdate) => {
      await withToast(updateProject(id, data), { success: 'Project saved' })
      reload()
    },
    [reload, withToast],
  )

  const remove = useCallback(
    async (id: number) => {
      await withToast(deleteProject(id), { success: 'Project moved to trash' })
      reload()
    },
    [reload, withToast],
  )

  return { projects, loading, error, create, update, remove, reload }
}
