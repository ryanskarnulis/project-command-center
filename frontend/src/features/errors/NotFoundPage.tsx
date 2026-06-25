import { Link } from 'react-router-dom'

/**
 * Friendly in-app 404. Rendered both by the catch-all `*` route (so it keeps the
 * app shell around it) and by the route error boundary for 404 responses. Uses the
 * shared `empty-state` styling so it reads as a normal page, not a raw error dump.
 */
export function NotFoundPage() {
  return (
    <main>
      <div className="section-heading">
        <h1>Page not found</h1>
      </div>
      <div className="empty-state">
        That page doesn’t exist — it may have moved or been removed.{' '}
        <Link to="/dashboard">Go to the dashboard</Link>.
      </div>
    </main>
  )
}
