import { useEffect, useState } from 'react'
import { Repeat, SkipForward } from 'lucide-react'
import { Link } from 'react-router-dom'
import { getTaskSeries, stopRecurrence } from '../../api/tasks'
import type { Task } from '../../types/task'
import { formatDueDate } from '../../utils/dates'
import { useTaskLinkTo } from './panel/taskPanelContext'

interface RecurrenceSeriesProps {
  task: Task
  /** Called with the updated task after recurrence is stopped, so the parent
   *  page can refresh its repeat badge and hide the stop affordance. */
  onStopped: (updated: Task) => void
  /** Opens the detail view's skip-occurrence confirm for the current occurrence,
   *  so skip is reachable from the timeline as well as the header. */
  onSkip: () => void
}

function occurrenceState(o: Task): { label: string; className: string } {
  // A skipped occurrence is soft-deleted rather than marked done, so check that
  // first. deleted_at alone is enough to mean "skipped" here only because the
  // series endpoint returns active and skipped rows and nothing else — a normally
  // trashed occurrence never reaches this list (services/task_recurrence.get_series).
  if (o.deleted_at) return { label: 'Skipped', className: 'workflow-skipped' }
  if (o.workflow_status === 'in_progress')
    return { label: 'In progress', className: 'workflow-in_progress' }
  if (o.workflow_status === 'done')
    return { label: 'Done', className: 'workflow-done' }
  return { label: 'Open', className: 'workflow-open' }
}

/**
 * Identity of the server-side series as far as this task can tell: any of these
 * changing means the occurrence list we hold may no longer match the backend.
 * Completing the occurrence spawns its successor, "Stop recurrence" ends the
 * chain, and an edit-scope-"future" change moves sibling due dates — all three
 * land in `workflow_status` / `repeat_interval` / `updated_at`, and `updated_at`
 * also covers the rest (a retitled sibling is rendered in the list).
 *
 * Flattened to a string so it can key an effect: `repeat_interval` is an object
 * whose identity changes on every parent render, which as a raw dependency would
 * refetch in a loop. (issue #259)
 */
function seriesKeyFor(task: Task): string {
  const repeat = task.repeat_interval
  const cadence = repeat ? `${repeat.unit}:${repeat.every}` : 'none'
  return `${task.id}|${task.updated_at}|${task.workflow_status}|${cadence}`
}

/** Series timeline + "Stop recurrence" for a task that belongs to a recurrence
 *  chain. The occurrence list is fetched lazily on expand, and refetched
 *  whenever the panel is open and the task changes underneath it. */
export function RecurrenceSeries({ task, onStopped, onSkip }: RecurrenceSeriesProps) {
  // Skip is offered on the current occurrence only while the series is live and
  // this occurrence is still open (not done, not already skipped).
  const canSkipCurrent =
    task.repeat_interval !== null &&
    task.workflow_status !== 'done' &&
    !task.deleted_at
  const taskLinkTo = useTaskLinkTo()
  const [expanded, setExpanded] = useState(false)
  const [series, setSeries] = useState<{ key: string; occurrences: Task[] } | null>(null)
  const [loadError, setLoadError] = useState<{ key: string; message: string } | null>(null)
  const [stopError, setStopError] = useState<string | null>(null)
  const [confirmingStop, setConfirmingStop] = useState(false)
  const [stopping, setStopping] = useState(false)

  const taskId = task.id
  const seriesKey = seriesKeyFor(task)
  // Both the list and its failure are tagged with the key they belong to, so a
  // result left over from a previous revision (or from a previous task, when the
  // peek panel repoints without remounting) is dropped rather than shown stale.
  const occurrences = series?.key === seriesKey ? series.occurrences : null
  const loadFailure = loadError?.key === seriesKey ? loadError.message : null
  // Derived rather than stored: while the panel is open with neither a matching
  // list nor a matching failure, a load is by definition in flight.
  const loading = expanded && occurrences === null && loadFailure === null

  // Load whenever the panel is open, re-running on every expand and on every
  // change to the task that could have moved the series. There is deliberately
  // no "already fetched" short-circuit — the previous one-shot cache was the
  // whole bug (issue #259).
  useEffect(() => {
    if (!expanded) return
    let cancelled = false
    getTaskSeries(taskId)
      .then((loaded) => {
        if (cancelled) return
        setSeries({ key: seriesKey, occurrences: loaded.occurrences })
        setLoadError(null)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setSeries(null)
        setLoadError({
          key: seriesKey,
          message: e instanceof Error ? e.message : 'Failed to load series',
        })
      })
    // A superseded or collapsed load must not land.
    return () => {
      cancelled = true
    }
  }, [expanded, taskId, seriesKey])

  async function handleStop() {
    setConfirmingStop(false)
    setStopping(true)
    setStopError(null)
    try {
      const updated = await stopRecurrence(task.id)
      onStopped(updated)
    } catch (e: unknown) {
      setStopError(e instanceof Error ? e.message : 'Failed to stop recurrence')
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
          <button
            type="button"
            onClick={() => setExpanded((open) => !open)}
            aria-expanded={expanded}
          >
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
            Stop this recurrence? No occurrence in this series will create the
            next one. Past occurrences are kept.
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

      {(stopError ?? loadFailure) && (
        <p role="alert" className="error">{stopError ?? loadFailure}</p>
      )}

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
                      <span className="recurrence-occurrence-here">
                        This occurrence
                        {canSkipCurrent && (
                          <button
                            type="button"
                            className="recurrence-occurrence-skip"
                            onClick={onSkip}
                          >
                            <SkipForward size={13} aria-hidden="true" />
                            Skip
                          </button>
                        )}
                      </span>
                    ) : o.deleted_at ? (
                      <span className="recurrence-occurrence-title">{o.title}</span>
                    ) : (
                      <Link to={taskLinkTo(o.id)} className="recurrence-occurrence-title">
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
