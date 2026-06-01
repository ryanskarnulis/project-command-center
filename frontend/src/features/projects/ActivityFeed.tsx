import { useProjectActivity } from './useProjectActivity'

interface ActivityFeedProps {
  projectId: number
  refreshKey?: number
}

export function ActivityFeed({ projectId, refreshKey }: ActivityFeedProps) {
  const { events, loading, error } = useProjectActivity(projectId, refreshKey)

  return (
    <section className="activity-feed">
      <h2>Activity</h2>
      {loading && <p>Loading…</p>}
      {error && <p role="alert">{error}</p>}
      {!loading && !error && events.length === 0 && <p>No activity yet.</p>}
      <ul>
        {events.map((e) => (
          <li key={e.id} className={`activity-item activity-${e.action}`}>
            <span>{e.summary}</span>{' '}
            <time className="activity-time" dateTime={e.created_at}>
              {new Date(e.created_at).toLocaleString()}
            </time>
          </li>
        ))}
      </ul>
    </section>
  )
}
