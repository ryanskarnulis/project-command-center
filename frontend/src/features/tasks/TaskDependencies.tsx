import { useMemo, useState } from 'react'
import type { Task } from '../../types/task'
import { useTaskDependencies } from './useTaskDependencies'

interface Props {
  task: Task
  tasks: Task[]
}

/** "Depends on" manager: B must be done before this task can start. */
export function TaskDependencies({ task, tasks }: Props) {
  const { dependencies, loading, error, add, remove } = useTaskDependencies(task.id)
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

  return (
    <section className="task-dependencies">
      <label>Depends on (must be done first)</label>
      {loading && <p>Loading…</p>}
      {error && <p role="alert">{error}</p>}
      <ul>
        {dependencies.map((d) => (
          <li key={d.id}>
            {d.depends_on_title}{' '}
            {d.depends_on_done ? (
              <span className="dep-done">✓ done</span>
            ) : (
              <span className="dep-pending">pending</span>
            )}{' '}
            <button type="button" onClick={() => void remove(d.id)}>
              Remove
            </button>
          </li>
        ))}
      </ul>
      {dependencies.length === 0 && !loading && <p>No dependencies.</p>}

      <select
        aria-label="Add dependency"
        value={selected}
        onChange={(e) => setSelected(e.target.value)}
      >
        <option value="">— add a dependency —</option>
        {options.map((t) => (
          <option key={t.id} value={String(t.id)}>
            {t.title}
          </option>
        ))}
      </select>{' '}
      <button type="button" disabled={selected === ''} onClick={() => void handleAdd()}>
        Add
      </button>
      {addError && <p role="alert">{addError}</p>}
    </section>
  )
}
