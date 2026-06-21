import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useProjectActivity } from './useProjectActivity'

interface ActivityFeedProps {
  projectId: number
  refreshKey?: number
}

export function ActivityFeed({ projectId, refreshKey }: ActivityFeedProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <section className="activity-feed">
      <button
        type="button"
        className="activity-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? (
          <ChevronDown size={16} aria-hidden="true" />
        ) : (
          <ChevronRight size={16} aria-hidden="true" />
        )}
        <span>Activity</span>
      </button>
      {expanded && (
        <ActivityFeedBody projectId={projectId} refreshKey={refreshKey} />
      )}
    </section>
  )
}

function ActivityFeedBody({ projectId, refreshKey }: ActivityFeedProps) {
  const { events, loading, error } = useProjectActivity(projectId, refreshKey)

  return (
    <>
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
    </>
  )
}
