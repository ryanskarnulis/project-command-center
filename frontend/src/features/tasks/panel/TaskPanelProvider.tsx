import type { ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { TaskPanelContext, type TaskPanelApi } from './taskPanelContext'
import { TaskPeekPanel } from './TaskPeekPanel'

const TASK_PARAM = 'task'

interface Props {
  /** Host refresh after a panel mutation, so the list behind stays current. */
  onMutated?: () => void
  children: ReactNode
}

/**
 * Hosts the slide-over task panel on a list page. Panel state lives entirely
 * in the `?task=<id>` search param: opening pushes history (Back closes the
 * panel and stays on the list), and a direct hit on the URL deep-links.
 */
export function TaskPanelProvider({ onMutated, children }: Props) {
  const [searchParams, setSearchParams] = useSearchParams()
  const raw = searchParams.get(TASK_PARAM)
  const openTaskId = raw !== null && /^\d+$/.test(raw) ? Number(raw) : null

  function withTask(taskId: number): URLSearchParams {
    const next = new URLSearchParams(searchParams)
    next.set(TASK_PARAM, String(taskId))
    return next
  }

  const api: TaskPanelApi = {
    openTaskId,
    taskLinkTo: (taskId) => ({ search: `?${withTask(taskId).toString()}` }),
    openTask: (taskId, opts) =>
      setSearchParams(withTask(taskId), { replace: opts?.replace ?? false }),
    closePanel: () => {
      const next = new URLSearchParams(searchParams)
      next.delete(TASK_PARAM)
      setSearchParams(next)
    },
  }

  return (
    <TaskPanelContext.Provider value={api}>
      {children}
      {openTaskId !== null && (
        <TaskPeekPanel
          taskId={openTaskId}
          onClose={api.closePanel}
          onMutated={onMutated}
        />
      )}
    </TaskPanelContext.Provider>
  )
}
