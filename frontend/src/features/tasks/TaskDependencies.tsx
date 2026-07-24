import { useMemo, useState } from 'react'
import { X } from 'lucide-react'
import { Link } from 'react-router-dom'
import { fireAndForget } from '../../utils/async'
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

  // Candidates: every other task that isn't already a dependency.
  const dependsOnIds = useMemo(
    () => new Set(dependencies.map((d) => d.depends_on_task_id)),
    [dependencies],
  )
  const options = tasks.filter(
    (t) => t.id !== task.id && !dependsOnIds.has(t.id),
  )

  async function handleAdd() {
    if (selected === '') return
    setAddError(null)
    try {
      await add(Number(selected))
      setSelected('')
    } catch (e: unknown) {
      setAddError(e instanceof Error ? e.message : 'Could not add dependency')
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
              onClick={() => fireAndForget(remove(d.id))}
            >
              <X size={16} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
      {dependencies.length === 0 && !loading && <p>No dependencies.</p>}

      <div className="dependency-add-row">
        <select
          aria-label="Add dependency"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
        >
          <option value="">Add a dependency</option>
          {options.map((t) => (
            <option key={t.id} value={String(t.id)}>
              {t.title}
            </option>
          ))}
        </select>
        <button type="button" disabled={selected === ''} onClick={() => void handleAdd()}>
          Add
        </button>
      </div>
      {addError && <p role="alert">{addError}</p>}
    </section>
  )
}
