import { Navigate, useParams } from 'react-router-dom'

/**
 * Legacy /tasks/:taskId deep links (command search, calendar, old bookmarks)
 * land on the Tasks page with the peek panel open over the list.
 */
export function TaskDetailRedirect() {
  const { taskId } = useParams<{ taskId: string }>()
  return <Navigate to={{ pathname: '/tasks', search: `?task=${taskId}` }} replace />
}
