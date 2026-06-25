import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../../api/client'
import { purgeInbox, restoreInbox } from '../../api/inbox'
import { listProjects, purgeProject, restoreProject } from '../../api/projects'
import { purgeTask, restoreTask } from '../../api/tasks'
import { purgeTrainingExample, restoreTrainingExample } from '../../api/training'
import { emptyTrash, getTrash } from '../../api/trash'
import type { Task } from '../../types/task'
import type { Trash } from '../../types/trash'
import { useTrashCount } from './trashCountContext'

const EMPTY: Trash = { projects: [], tasks: [], inbox_items: [], training_examples: [] }

const INBOX_409 =
  'That note was re-captured after it was dismissed — the active copy already represents it.'

export type TrashKind = 'projects' | 'tasks' | 'inbox' | 'training'

export interface RestoreItem {
  id: number
  label: string
  /** For projects: tasks cascade-deleted with it, offered for restore. */
  archivedTaskCount?: number
}

interface UseTrash {
  trash: Trash
  loading: boolean
  error: string | null
  notice: string | null
  restoreProjectById: (id: number, name: string, archivedTaskCount: number) => Promise<void>
  restoreTaskById: (id: number, title: string) => Promise<void>
  restoreInboxById: (id: number, label: string) => Promise<void>
  restoreTrainingById: (id: number, label: string) => Promise<void>
  restoreAll: (kind: TrashKind, items: RestoreItem[]) => Promise<void>
  purgeById: (kind: TrashKind, id: number, label: string) => Promise<void>
  emptyTrashAll: () => Promise<void>
}

const RESTORE: Record<TrashKind, (id: number) => Promise<unknown>> = {
  projects: restoreProject,
  tasks: restoreTask,
  inbox: restoreInbox,
  training: restoreTrainingExample,
}

const PURGE: Record<TrashKind, (id: number) => Promise<void>> = {
  projects: purgeProject,
  tasks: purgeTask,
  inbox: purgeInbox,
  training: purgeTrainingExample,
}

export function useTrash(): UseTrash {
  const [trash, setTrash] = useState<Trash>(EMPTY)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  // Active project id → name, so a restored task's notice can name where it
  // actually landed (its original project, or General if that was deleted).
  const [projectNames, setProjectNames] = useState<Map<number, string>>(new Map())
  const { refresh: refreshCount } = useTrashCount()

  useEffect(() => {
    let active = true
    listProjects()
      .then((projects) => {
        if (active) setProjectNames(new Map(projects.map((p) => [p.id, p.name])))
      })
      .catch(() => {
        // Best-effort: without the map the task notice omits the destination name.
      })
    return () => {
      active = false
    }
  }, [])

  const reload = useCallback(() => {
    setRefreshKey((k) => k + 1)
    void refreshCount()
  }, [refreshCount])

  useEffect(() => {
    let active = true
    getTrash()
      .then((data) => {
        if (!active) return
        setTrash(data)
        // Don't clear error here: a reload is only ever triggered by an action
        // that already reset error/notice at its start, and a 409/failure set
        // after that reload must survive the refetch that follows it.
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
    async (kind: TrashKind, id: number, buildNotice: (result: unknown) => string) => {
      setError(null)
      setNotice(null)
      try {
        const result = await RESTORE[kind](id)
        setNotice(buildNotice(result))
        reload()
      } catch (e: unknown) {
        // A dismissed inbox item can 409 if the same text was re-captured since.
        if (e instanceof ApiError && e.status === 409) {
          setError(INBOX_409)
          reload()
          return
        }
        setError(e instanceof Error ? e.message : 'Failed to restore item')
      }
    },
    [reload],
  )

  const restoreProjectById = useCallback(
    async (id: number, name: string, archivedTaskCount: number) => {
      // A deleted project takes its tasks into the trash with it; ask whether to
      // bring them back too.
      const bringTasks =
        archivedTaskCount > 0 &&
        window.confirm(
          `Bring back ${archivedTaskCount} task${archivedTaskCount === 1 ? '' : 's'} with “${name}”?`,
        )
      setError(null)
      setNotice(null)
      try {
        const { restored_task_count } = await restoreProject(id, bringTasks)
        setNotice(
          restored_task_count > 0
            ? `Restored project “${name}” with ${restored_task_count} task${restored_task_count === 1 ? '' : 's'}.`
            : `Restored project “${name}”.`,
        )
        reload()
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Failed to restore item')
      }
    },
    [reload],
  )
  const restoreTaskById = useCallback(
    (id: number, title: string) =>
      runRestore('tasks', id, (result) => {
        const projectId = (result as Task).project_id
        const dest = projectId !== null ? projectNames.get(projectId) : null
        return dest ? `Restored “${title}” to “${dest}”.` : `Restored “${title}”.`
      }),
    [runRestore, projectNames],
  )
  const restoreInboxById = useCallback(
    (id: number, label: string) => runRestore('inbox', id, () => `Restored note “${label}”.`),
    [runRestore],
  )
  const restoreTrainingById = useCallback(
    (id: number, label: string) =>
      runRestore('training', id, () => `Restored training example “${label}”.`),
    [runRestore],
  )

  const restoreAll = useCallback(
    async (kind: TrashKind, items: RestoreItem[]) => {
      setError(null)
      setNotice(null)
      // Projects cascade-delete their tasks; offer once to bring them all back.
      const totalArchived =
        kind === 'projects'
          ? items.reduce((sum, item) => sum + (item.archivedTaskCount ?? 0), 0)
          : 0
      const bringTasks =
        totalArchived > 0 &&
        window.confirm(
          `Bring back ${totalArchived} task${totalArchived === 1 ? '' : 's'} with ${
            items.length === 1 ? 'this project' : 'these projects'
          }?`,
        )
      let restored = 0
      let skipped = 0
      let restoredTasks = 0
      let failed = false
      for (const item of items) {
        try {
          if (kind === 'projects') {
            const { restored_task_count } = await restoreProject(item.id, bringTasks)
            restoredTasks += restored_task_count
          } else {
            await RESTORE[kind](item.id)
          }
          restored += 1
        } catch (e: unknown) {
          // Inbox re-capture races 409 — skip and keep going.
          if (e instanceof ApiError && e.status === 409) {
            skipped += 1
            continue
          }
          failed = true
          setError(e instanceof Error ? e.message : 'Failed to restore items')
          break
        }
      }
      reload()
      if (restored > 0) {
        const noun =
          kind === 'inbox'
            ? 'note'
            : kind === 'training'
              ? 'training example'
              : kind.slice(0, -1)
        const parts = [`Restored ${restored} ${noun}${restored === 1 ? '' : 's'}.`]
        if (kind === 'projects' && restoredTasks > 0)
          parts.push(`Brought back ${restoredTasks} task${restoredTasks === 1 ? '' : 's'}.`)
        if (skipped > 0) parts.push(`${skipped} re-captured and skipped.`)
        setNotice(parts.join(' '))
      } else if (skipped > 0 && !failed) {
        setError(INBOX_409)
      }
    },
    [reload],
  )

  const purgeById = useCallback(
    async (kind: TrashKind, id: number, label: string) => {
      setError(null)
      setNotice(null)
      try {
        await PURGE[kind](id)
        setNotice(`Permanently deleted “${label}”.`)
        reload()
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Failed to delete item')
      }
    },
    [reload],
  )

  const emptyTrashAll = useCallback(async () => {
    setError(null)
    setNotice(null)
    try {
      const result = await emptyTrash()
      const total =
        result.projects + result.tasks + result.inbox_items + result.training_examples
      setNotice(
        total === 0
          ? 'Trash was already empty.'
          : `Permanently deleted ${total} item${total === 1 ? '' : 's'}.`,
      )
      reload()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to empty trash')
    }
  }, [reload])

  return {
    trash,
    loading,
    error,
    notice,
    restoreProjectById,
    restoreTaskById,
    restoreInboxById,
    restoreTrainingById,
    restoreAll,
    purgeById,
    emptyTrashAll,
  }
}
