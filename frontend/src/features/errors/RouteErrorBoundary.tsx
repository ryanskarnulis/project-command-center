import { Link, isRouteErrorResponse, useRouteError } from 'react-router-dom'
import { NotFoundPage } from './NotFoundPage'

/**
 * Catches errors thrown during routing (loaders, render, unmatched 404 responses)
 * and renders a friendly recoverable page instead of React Router's developer
 * default. A 404 response falls through to {@link NotFoundPage}; anything else is a
 * generic "something went wrong" with a way back. Raw error details are logged for
 * dev, never shown to the user.
 */
export function RouteErrorBoundary() {
  const error = useRouteError()

  if (isRouteErrorResponse(error) && error.status === 404) {
    return <NotFoundPage />
  }

  console.error('Route error:', error)

  return (
    <main>
      <div className="section-heading">
        <h1>Something went wrong</h1>
      </div>
      <div className="empty-state">
        We hit an unexpected error rendering this page.{' '}
        <Link to="/dashboard">Return to the dashboard</Link>.
      </div>
    </main>
  )
}
