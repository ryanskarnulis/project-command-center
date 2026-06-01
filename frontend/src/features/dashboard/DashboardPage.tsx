import { Link } from 'react-router-dom'
import { useDashboard } from './useDashboard'

export function DashboardPage() {
  const { overview, loading, error, summaries, summarize } = useDashboard()

  if (loading) return <p>Loading dashboard…</p>
  if (error) return <p className="error">Error: {error}</p>
  if (!overview) return null

  return (
    <div className="dashboard">
      <h1>Dashboard</h1>

      <section className="dashboard-stat">
        <span className="stat-value">{overview.total_open_tasks}</span>
        <span className="stat-label"> open tasks</span>
      </section>

      <section>
        <h2>Projects</h2>
        {overview.projects.length === 0 ? (
          <p>No projects yet. <Link to="/projects">Create one.</Link></p>
        ) : (
          <ul className="project-list">
            {overview.projects.map((row) => {
              const state = summaries[row.project_id]
              return (
                <li key={row.project_id} className="project-row">
                  <div className="project-row-header">
                    <Link to={`/projects/${row.project_id}/tasks`}>
                      {row.project_name}
                    </Link>
                    <span className="task-count">{row.open_task_count} open</span>
                    <button
                      onClick={() => summarize(row.project_id)}
                      disabled={state?.loading}
                    >
                      {state?.loading ? 'Summarizing…' : 'Summarize'}
                    </button>
                  </div>

                  {state?.data && (
                    <p className="project-summary">{state.data.summary}</p>
                  )}
                  {state?.error && (
                    <p className="error">Summary failed: {state.error}</p>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <section>
        <h2>Recent Inbox</h2>
        {overview.recent_inbox.length === 0 ? (
          <p>No inbox items. <Link to="/inbox">Add one.</Link></p>
        ) : (
          <ul className="inbox-list">
            {overview.recent_inbox.map((item) => (
              <li key={item.id} className="inbox-row">
                <Link
                  to={
                    item.resolved_project_id
                      ? `/projects/${item.resolved_project_id}/tasks`
                      : '/inbox'
                  }
                >
                  {item.summary ?? `Inbox #${item.id}`}
                </Link>
                <span className="inbox-meta">
                  {item.source}
                  {item.reviewed_at
                    ? ' · reviewed'
                    : item.processed_at
                      ? ' · awaiting review'
                      : ' · not processed'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
