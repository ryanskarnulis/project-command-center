import { useState } from 'react'
import { Repeat } from 'lucide-react'
import { Link } from 'react-router-dom'
import { getTaskSeries, stopRecurrence } from '../../api/tasks'
import type { Task } from '../../types/task'
import { formatDueDate } from '../../utils/dates'

interface RecurrenceSeriesProps {
  task: Task
  /** Called with the updated task after recurrence is stopped, so the parent
   *  page can refresh its repeat badge and hide the stop affordance. */
  onStopped: (updated: Task) => void
}

function occurrenceState(o: Task): { label: string; className: string } {
  // Skipped occurrences are soft-deleted (not marked done), so check that first.
  if (o.deleted_at) return { label: 'Skipped', className: 'workflow-skipped' }
  if (o.workflow_status === 'in_progress')
    return { label: 'In progress', className: 'workflow-in_progress' }
  if (o.workflow_status === 'done')
    return { label: 'Done', className: 'workflow-done' }
  return { label: 'Open', className: 'workflow-open' }
}

/** Series timeline + "Stop recurrence" for a task that belongs to a recurrence
 *  chain. The occurrence list is fetched lazily on first expand. */
export function RecurrenceSeries({ task, onStopped }: RecurrenceSeriesProps) {
  const [expanded, setExpanded] = useState(false)
  const [occurrences, setOccurrences] = useState<Task[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmingStop, setConfirmingStop] = useState(false)
  const [stopping, setStopping] = useState(false)

  async function toggle() {
    const next = !expanded
    setExpanded(next)
    if (next && occurrences === null && !loading) {
      setLoading(true)
      setError(null)
      try {
        const series = await getTaskSeries(task.id)
        setOccurrences(series.occurrences)
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Failed to load series')
      } finally {
        setLoading(false)
      }
    }
  }

  async function handleStop() {
    setConfirmingStop(false)
    setStopping(true)
    setError(null)
    try {
      const updated = await stopRecurrence(task.id)
      onStopped(updated)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to stop recurrence')
    } finally {
      setStopping(false)
    }
  }

  return (
    <section className="task-detail-panel recurrence-series-panel">
      <div className="task-section-heading">
        <h2>
          <Repeat size={16} aria-hidden="true" /> Recurrence
        </h2>
        <div className="task-section-actions">
          <button type="button" onClick={() => void toggle()} aria-expanded={expanded}>
            {expanded ? 'Hide occurrences' : 'Show occurrences'}
          </button>
          {task.repeat_interval && (
            <button
              type="button"
              className="danger-action"
              disabled={stopping}
              onClick={() => setConfirmingStop(true)}
            >
              {stopping ? 'Stopping…' : 'Stop recurrence'}
            </button>
          )}
        </div>
      </div>

      {!task.repeat_interval && (
        <p className="recurrence-stopped-note">
          This series has been stopped — no further occurrences will be created.
        </p>
      )}

      {confirmingStop && (
        <div className="skip-confirm" role="alertdialog" aria-label="Confirm stop recurrence">
          <p>
            Stop this recurrence? Completing this task will no longer create the
            next occurrence. Past occurrences are kept.
          </p>
          <div className="skip-confirm-actions">
            <button type="button" onClick={() => void handleStop()}>
              Stop recurrence
            </button>
            <button type="button" onClick={() => setConfirmingStop(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && <p role="alert" className="error">{error}</p>}

      {expanded && (
        <>
          {loading && <p>Loading occurrences…</p>}
          {occurrences && (
            <ul className="recurrence-occurrence-list">
              {occurrences.map((o) => {
                const state = occurrenceState(o)
                const isCurrent = o.id === task.id
                return (
                  <li
                    key={o.id}
                    className={isCurrent ? 'recurrence-occurrence current' : 'recurrence-occurrence'}
                    aria-current={isCurrent ? 'true' : undefined}
                  >
                    <span className="recurrence-occurrence-due">
                      {o.due_date ? formatDueDate(o.due_date) : 'No due date'}
                    </span>
                    <span className={`status-pill ${state.className}`}>{state.label}</span>
                    {isCurrent ? (
                      <span className="recurrence-occurrence-here">This occurrence</span>
                    ) : o.deleted_at ? (
                      <span className="recurrence-occurrence-title">{o.title}</span>
                    ) : (
                      <Link to={`/tasks/${o.id}`} className="recurrence-occurrence-title">
                        {o.title}
                      </Link>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </>
      )}
    </section>
  )
}
