import { useMemo, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { Link } from 'react-router-dom'
import { apiErrorMessage } from '../../api/errorMessage'
import type { Task } from '../../types/task'
import { useTaskLinkTo } from './panel/taskPanelContext'
import { useTaskDependencies } from './useTaskDependencies'

interface Props {
  task: Task
  tasks: Task[]
}

/** "Depends on" manager: B must be done before this task can start. */
export function TaskDependencies({ task, tasks }: Props) {
  const taskLinkTo = useTaskLinkTo()
  const { dependencies, dependents, loading, error, add, remove } =
    useTaskDependencies(task.id)
  const [selected, setSelected] = useState('')
  const [addError, setAddError] = useState<string | null>(null)
  const [removeError, setRemoveError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [removingIds, setRemovingIds] = useState<number[]>([])
  // State updates don't land until the next render, so two clicks fired in the
  // same tick would both pass an `adding`/`removingIds` check and dispatch two
  // mutations — the second coming back 409 (duplicate) or 404 (already gone)
  // for a change that did commit. The refs close that gap. Removal is tracked
  // per dependency id so one in-flight delete doesn't freeze the other rows.
  const addingRef = useRef(false)
  const removingRef = useRef(new Set<number>())

  // Candidates: every other task that isn't already a dependency.
  const dependsOnIds = useMemo(
    () => new Set(dependencies.map((d) => d.depends_on_task_id)),
    [dependencies],
  )
  const options = tasks.filter(
    (t) => t.id !== task.id && !dependsOnIds.has(t.id),
  )

  async function handleAdd() {
    if (addingRef.current || selected === '') return
    addingRef.current = true
    setAdding(true)
    setAddError(null)
    try {
      await add(Number(selected))
      setSelected('')
    } catch (e: unknown) {
      setAddError(apiErrorMessage(e, 'Could not add dependency'))
    } finally {
      // Released on failure too, so a rejected add stays retryable.
      addingRef.current = false
      setAdding(false)
    }
  }

  // Mirrors `handleAdd`: a failed removal leaves the row on screen, so the
  // reason has to be shown or the button just looks broken. The list is only
  // reloaded by the hook on success, so the row stays and the click is
  // retryable.
  async function handleRemove(dependencyId: number) {
    if (removingRef.current.has(dependencyId)) return
    removingRef.current.add(dependencyId)
    setRemovingIds((ids) => [...ids, dependencyId])
    setRemoveError(null)
    try {
      await remove(dependencyId)
    } catch (e: unknown) {
      setRemoveError(apiErrorMessage(e, 'Could not remove dependency'))
    } finally {
      removingRef.current.delete(dependencyId)
      setRemovingIds((ids) => ids.filter((id) => id !== dependencyId))
    }
  }

  const showDependents = task.is_blocking || dependents.length > 0
  const downstreamLabel = `${task.blocked_task_count} downstream ${
    task.blocked_task_count === 1 ? 'task' : 'tasks'
  } waiting`

  return (
    <section className="task-dependencies">
      {showDependents && (
        <>
          <div className="task-section-heading">
            <h2>Blocking</h2>
            <span>{task.is_blocking ? downstreamLabel : 'Downstream tasks'}</span>
          </div>
          <ul className="dependency-list">
            {dependents.map((d) => (
              <li key={d.id}>
                <Link to={taskLinkTo(d.dependent_task_id)}>
                  {d.dependent_title}
                </Link>
                {d.dependent_done ? (
                  <span className="dep-done">✓ done</span>
                ) : (
                  <span className="dep-pending">waiting</span>
                )}
              </li>
            ))}
          </ul>
          {dependents.length === 0 && !loading && <p>No dependents.</p>}
        </>
      )}

      <div className="task-section-heading">
        <h2>Dependencies</h2>
        <span>Must be done first</span>
      </div>
      {loading && <p>Loading…</p>}
      {error && <p role="alert">{error}</p>}
      <ul className="dependency-list">
        {dependencies.map((d) => (
          <li key={d.id}>
            <Link to={taskLinkTo(d.depends_on_task_id)}>{d.depends_on_title}</Link>
            {d.depends_on_done ? (
              <span className="dep-done">✓ done</span>
            ) : (
              <span className="dep-pending">pending</span>
            )}
            <button
              type="button"
              className="icon-button compact"
              aria-label={`Remove dependency ${d.depends_on_title}`}
              disabled={removingIds.includes(d.id)}
              onClick={() => void handleRemove(d.id)}
            >
              <X size={16} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
      {removeError && <p role="alert">{removeError}</p>}
      {dependencies.length === 0 && !loading && <p>No dependencies.</p>}

      <div className="dependency-add-row">
        <select
          aria-label="Add dependency"
          value={selected}
          disabled={adding}
          onChange={(e) => setSelected(e.target.value)}
        >
          <option value="">Add a dependency</option>
          {options.map((t) => (
            <option key={t.id} value={String(t.id)}>
              {t.title}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={adding || selected === ''}
          onClick={() => void handleAdd()}
        >
          {adding ? 'Adding…' : 'Add'}
        </button>
      </div>
      {addError && <p role="alert">{addError}</p>}
    </section>
  )
}
