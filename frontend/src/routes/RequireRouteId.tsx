import type { ReactElement } from 'react'
import { useParams } from 'react-router-dom'
import { NotFoundPage } from '../features/errors/NotFoundPage'
import { isValidRouteId } from '../utils/routeParams'

type Props = {
  /** Name of the dynamic segment to validate, e.g. `projectId`. */
  param: string
  children: ReactElement
}

/**
 * Route boundary guard: a dynamic id segment must be a positive integer before
 * the page behind it mounts. Malformed ids (`/projects/nope`, `/agent/0`)
 * render the same in-app Not Found surface as any unknown route instead of
 * letting `Number(...)` produce NaN deep inside a page.
 */
export function RequireRouteId({ param, children }: Props) {
  const params = useParams()
  if (!isValidRouteId(params[param])) {
    return <NotFoundPage />
  }
  return children
}
