import { createContext, useContext } from 'react'
import type { To } from 'react-router-dom'

export interface TaskPanelApi {
  /** Task currently open in the peek panel, from the `?task=` search param. */
  openTaskId: number | null
  /** Link target that opens the panel over the current page (a real <Link>). */
  taskLinkTo: (taskId: number) => To
  /** Repoint the panel; `replace` when the previous task no longer exists (skip). */
  openTask: (taskId: number, opts?: { replace?: boolean }) => void
  closePanel: () => void
}

export const TaskPanelContext = createContext<TaskPanelApi | null>(null)

/** Null on pages without a TaskPanelProvider — callers fall back to /tasks/:id. */
export function useTaskPanel(): TaskPanelApi | null {
  return useContext(TaskPanelContext)
}

/**
 * Link target for a task: opens the peek panel in place on panel-hosting
 * pages, and falls back to the /tasks/:id deep link (which redirects to the
 * Tasks page with the panel open) everywhere else.
 */
export function useTaskLinkTo(): (taskId: number) => To {
  const panel = useTaskPanel()
  return (taskId) => (panel ? panel.taskLinkTo(taskId) : `/tasks/${taskId}`)
}
