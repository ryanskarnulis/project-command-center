import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../../api/client'
import { apiErrorMessage } from '../../api/errorMessage'
import { listProjects, purgeProject, restoreProject } from '../../api/projects'
import { purgeTask, restoreTask } from '../../api/tasks'
import {
  emptyTrash,
  getTrash,
  purgeSelected,
  restoreTrashedTask,
  undoProjectRestore,
  undoTaskRestore,
} from '../../api/trash'
import type { ProjectRestoreUndo, TaskRestoreUndo, Trash } from '../../types/trash'
import { useTrashCount } from './trashCountContext'

const EMPTY: Trash = { projects: [], tasks: [] }

export type TrashKind = 'projects' | 'tasks'

export interface RestoreItem {
  id: number
  label: string
  /** For projects: tasks cascade-deleted with it, offered for restore. */
  archivedTaskCount?: number
  /** For projects: every trashed task it owns — what a purge really destroys. */
  purgeTaskCount?: number
}

/**
 * What one restore changed, as the server reported it — the only thing Undo
 * may act on. Undo used to DELETE the clicked id, which cascades through
 * children restored beforehand (#307) and 404s after an un-skip that hands back
 * a different task (#306).
 */
export type RestoreUndo =
  | { kind: 'tasks'; receipt: TaskRestoreUndo }
  | { kind: 'projects'; receipt: ProjectRestoreUndo }

/** A row in the cross-kind selection both /trash trees build. */
export interface SelectedTrashItem extends RestoreItem {
  kind: TrashKind
}

interface UseTrash {
  trash: Trash
  loading: boolean
  error: string | null
  notice: string | null
  /**
   * Resolves to the restore's undo receipt when it landed, null when it failed.
   * The caller decides `bringTasks` up front: plain Restore brings a project's
   * archived tasks, and "Restore without its tasks" is its own action.
   */
  restoreProjectById: (id: number, name: string, bringTasks: boolean) => Promise<RestoreUndo | null>
  restoreTaskById: (id: number, title: string) => Promise<RestoreUndo | null>
  /** Cross-kind bulk restore with one combined notice; projects bring their tasks. */
  restoreItems: (items: SelectedTrashItem[]) => Promise<void>
  /** Cross-kind bulk purge in one server call. The caller has already confirmed. */
  purgeItems: (items: SelectedTrashItem[]) => Promise<void>
  /** The inverse of one restore: reverse exactly what its receipt says it changed. */
  undoRestore: (undo: RestoreUndo, label: string) => Promise<void>
  purgeById: (
    kind: TrashKind,
    id: number,
    label: string,
    cascadeTaskCount?: number,
  ) => Promise<void>
  emptyTrashAll: () => Promise<void>
}

/** Fallback for a restore that failed without an `Error` to speak for itself.
 * The interesting case is the API's `detail` (a recurring occurrence whose due
 * date is already taken by a live sibling), which `apiErrorMessage` prefers. */
const RESTORE_FAILED = 'Failed to restore item'

const RESTORE: Record<TrashKind, (id: number) => Promise<unknown>> = {
  projects: restoreProject,
  tasks: restoreTask,
}

const PURGE: Record<TrashKind, (id: number) => Promise<void>> = {
  projects: purgeProject,
  tasks: purgeTask,
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

  const restoreProjectById = useCallback(
    async (id: number, name: string, bringTasks: boolean): Promise<RestoreUndo | null> => {
      setError(null)
      setNotice(null)
      try {
        const { restored_task_count, undo } = await restoreProject(id, bringTasks)
        setNotice(
          restored_task_count > 0
            ? `Restored project “${name}” with ${restored_task_count} task${restored_task_count === 1 ? '' : 's'}.`
            : `Restored project “${name}”.`,
        )
        reload()
        return { kind: 'projects', receipt: undo }
      } catch (e: unknown) {
        setError(apiErrorMessage(e, RESTORE_FAILED))
        return null
      }
    },
    [reload],
  )
  const restoreTaskById = useCallback(
    async (id: number, title: string): Promise<RestoreUndo | null> => {
      setError(null)
      setNotice(null)
      try {
        // The Trash route, not the plain restore: it also says what the
        // restore changed, which is all Undo may reverse.
        const { task, undo } = await restoreTrashedTask(id)
        const dest = task.project_id !== null ? projectNames.get(task.project_id) : null
        setNotice(dest ? `Restored “${title}” to “${dest}”.` : `Restored “${title}”.`)
        reload()
        return { kind: 'tasks', receipt: undo }
      } catch (e: unknown) {
        setError(apiErrorMessage(e, RESTORE_FAILED))
        return null
      }
    },
    [reload, projectNames],
  )

  const restoreItems = useCallback(
    async (items: SelectedTrashItem[]) => {
      setError(null)
      setNotice(null)
      let attempted = 0
      let restored = 0
      let restoredTasks = 0
      let firstError: string | null = null
      for (const item of items) {
        attempted += 1
        try {
          if (item.kind === 'projects') {
            const { restored_task_count } = await restoreProject(
              item.id,
              (item.archivedTaskCount ?? 0) > 0,
            )
            restoredTasks += restored_task_count
          } else {
            await RESTORE[item.kind](item.id)
          }
          restored += 1
        } catch (e: unknown) {
          // Already gone from trash — restoring a skipped occurrence can purge a
          // subtask selected alongside it — so there's nothing left to restore,
          // and nothing to report (BUG-11).
          if (e instanceof ApiError && e.status === 404) {
            attempted -= 1
            continue
          }
          // One action can hit two endpoints and half-fail; keep going so the
          // notice can say exactly how far it got.
          firstError ??= apiErrorMessage(e, RESTORE_FAILED)
        }
      }
      reload()
      if (restored > 0) {
        const parts = [
          restored === attempted
            ? `Restored ${restored} item${restored === 1 ? '' : 's'}.`
            : `Restored ${restored} of ${attempted} items.`,
        ]
        if (restoredTasks > 0)
          parts.push(`Brought back ${restoredTasks} task${restoredTasks === 1 ? '' : 's'}.`)
        setNotice(parts.join(' '))
      }
      if (firstError) setError(firstError)
    },
    [reload],
  )

  const purgeItems = useCallback(
    async (items: SelectedTrashItem[]) => {
      setError(null)
      setNotice(null)
      try {
        const result = await purgeSelected({
          project_ids: items.filter((i) => i.kind === 'projects').map((i) => i.id),
          task_ids: items.filter((i) => i.kind === 'tasks').map((i) => i.id),
        })
        // `tasks` counts the selected tasks and every trashed task the selected
        // projects owned — the server reports the whole set it destroyed.
        const total = result.projects + result.tasks
        if (total > 0)
          setNotice(`Permanently deleted ${total} item${total === 1 ? '' : 's'}.`)
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Failed to delete items')
      }
      reload()
    },
    [reload],
  )

  const undoRestore = useCallback(
    async (undo: RestoreUndo, label: string) => {
      setError(null)
      setNotice(null)
      try {
        // Not an ordinary delete: that cascades through every active child,
        // including ones restored on their own before this restore (#307), and
        // an un-skip's inverse moves the series back rather than trashing the
        // live occurrence it handed back (#306). The server replays the receipt
        // and 409s if those rows have moved on since.
        if (undo.kind === 'projects') await undoProjectRestore(undo.receipt)
        else await undoTaskRestore(undo.receipt)
        setNotice(`Moved “${label}” back to the trash.`)
        reload()
      } catch (e: unknown) {
        setError(apiErrorMessage(e, 'Failed to undo the restore'))
      }
    },
    [reload],
  )

  const purgeById = useCallback(
    async (kind: TrashKind, id: number, label: string, purgeTaskCount = 0) => {
      setError(null)
      setNotice(null)
      try {
        await PURGE[kind](id)
        // Purging a project also destroys every trashed task it owns; the
        // single-item purge route returns no counts, so name the scope the UI
        // already knows about (BUG #184, #189).
        setNotice(
          purgeTaskCount > 0
            ? `Permanently deleted “${label}” and ${purgeTaskCount} task${purgeTaskCount === 1 ? '' : 's'}.`
            : `Permanently deleted “${label}”.`,
        )
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
      const total = result.projects + result.tasks
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
    restoreItems,
    purgeItems,
    undoRestore,
    purgeById,
    emptyTrashAll,
  }
}
