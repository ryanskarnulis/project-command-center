import type { ReactNode } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { TaskPanelContext, type TaskPanelApi } from './taskPanelContext'
import { TaskPeekPanel } from './TaskPeekPanel'

const TASK_PARAM = 'task'
/** Trail of tasks opened before the current one in this panel, oldest first. */
const FROM_PARAM = 'from'

function parseTrail(raw: string | null): number[] {
  if (raw === null || raw === '') return []
  const ids = raw.split(',')
  return ids.every((id) => /^\d+$/.test(id)) ? ids.map(Number) : []
}

interface Props {
  /** Host refresh after a panel mutation, so the list behind stays current. */
  onMutated?: () => void
  children: ReactNode
}

/**
 * Hosts the slide-over task panel on a list page. Panel state lives entirely
 * in the search params: `?task=<id>` is the open task, and `&from=<id>,…` is
 * the trail of tasks it was reached through (subtask, dependency, occurrence
 * links inside the panel). Opening pushes history (Back closes the panel and
 * stays on the list), a direct hit on the URL deep-links, and the panel's own
 * Back control walks the trail.
 */
export function TaskPanelProvider({ onMutated, children }: Props) {
  const [searchParams, setSearchParams] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const raw = searchParams.get(TASK_PARAM)
  const openTaskId = raw !== null && /^\d+$/.test(raw) ? Number(raw) : null
  const trail = openTaskId === null ? [] : parseTrail(searchParams.get(FROM_PARAM))

  /**
   * Params opening `taskId`. From an open panel the current task joins the
   * trail (unless `keepTrail`: a replace-open repoints the same slot); from
   * the list the trail starts empty.
   */
  function withTask(taskId: number, keepTrail = false): URLSearchParams {
    const next = new URLSearchParams(searchParams)
    next.set(TASK_PARAM, String(taskId))
    const nextTrail =
      openTaskId === null ? []
      : keepTrail || openTaskId === taskId ? trail
      : [...trail, openTaskId]
    if (nextTrail.length > 0) next.set(FROM_PARAM, nextTrail.join(','))
    else next.delete(FROM_PARAM)
    return next
  }

  const api: TaskPanelApi = {
    openTaskId,
    previousTaskId: trail.length > 0 ? trail[trail.length - 1] : null,
    taskLinkTo: (taskId) => ({ search: `?${withTask(taskId).toString()}` }),
    openTask: (taskId, opts) =>
      setSearchParams(withTask(taskId, opts?.replace ?? false), {
        replace: opts?.replace ?? false,
      }),
    goBack: () => {
      if (trail.length === 0) return
      // Every in-panel open pushed an entry, so history Back lands on the
      // previous task and keeps the browser's Back in step. A cold deep link
      // carrying a trail has nothing behind it: rewrite in place instead.
      if (location.key !== 'default') {
        navigate(-1)
        return
      }
      const next = new URLSearchParams(searchParams)
      next.set(TASK_PARAM, String(trail[trail.length - 1]))
      const rest = trail.slice(0, -1)
      if (rest.length > 0) next.set(FROM_PARAM, rest.join(','))
      else next.delete(FROM_PARAM)
      setSearchParams(next, { replace: true })
    },
    closePanel: () => {
      const next = new URLSearchParams(searchParams)
      next.delete(TASK_PARAM)
      next.delete(FROM_PARAM)
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
          onBack={api.previousTaskId !== null ? api.goBack : undefined}
          onMutated={onMutated}
        />
      )}
    </TaskPanelContext.Provider>
  )
}
