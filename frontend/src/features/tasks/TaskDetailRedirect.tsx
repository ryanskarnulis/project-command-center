import { useEffect, useState } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import { getTask } from '../../api/tasks'
import { NotFoundPage } from '../errors/NotFoundPage'

/** A resolution tagged with the task it was resolved for; `null` target = gone. */
interface Resolved {
  taskId: string
  target: string | null
}

/**
 * Legacy `/tasks/:taskId` deep links — command search, the agent's tool-call
 * receipts, the `useTaskLinkTo` fallback, old bookmarks — land on the task's
 * project page with the peek panel open over the list.
 *
 * Unlike the redirect this replaced, the target isn't derivable from the URL:
 * it needs the task's project, so this fetches the task first. That resolution
 * is always possible because tasks are always filed (`project_id` is NOT NULL
 * since 93bfbc8f40ab) — there is no unfiled case left to strand a link.
 */
export function TaskDetailRedirect() {
  const { taskId } = useParams<{ taskId: string }>()
  const [resolved, setResolved] = useState<Resolved | null>(null)

  useEffect(() => {
    let cancelled = false
    getTask(Number(taskId))
      .then((task) => {
        if (!cancelled) {
          setResolved({
            taskId: String(taskId),
            target: `/projects/${task.project_id}/tasks?task=${task.id}`,
          })
        }
      })
      .catch(() => {
        if (!cancelled) setResolved({ taskId: String(taskId), target: null })
      })
    return () => {
      cancelled = true
    }
  }, [taskId])

  // Derived rather than reset in the effect: a resolution for a previous id
  // must not redirect this one (same rule as `useTasks`' loaded scope).
  const current = resolved?.taskId === taskId ? resolved : null

  if (current === null) {
    return (
      <main>
        <p>Loading…</p>
      </main>
    )
  }
  // A trashed or purged task has no project page to open: the deep link is as
  // dead as any other stale URL, so it reads as one.
  if (current.target === null) return <NotFoundPage />
  return <Navigate to={current.target} replace />
}
