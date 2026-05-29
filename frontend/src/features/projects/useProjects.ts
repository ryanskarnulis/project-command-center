import { useCallback, useEffect, useState } from 'react'
import { createProject, deleteProject, listProjects } from '../../api/projects'
import type { Project, ProjectCreate } from '../../types/project'

interface UseProjects {
  projects: Project[]
  loading: boolean
  error: string | null
  create: (data: ProjectCreate) => Promise<void>
  remove: (id: number) => Promise<void>
  reload: () => void
}

export function useProjects(): UseProjects {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

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
      await createProject(data)
      reload()
    },
    [reload],
  )

  const remove = useCallback(
    async (id: number) => {
      await deleteProject(id)
      reload()
    },
    [reload],
  )

  return { projects, loading, error, create, remove, reload }
}
