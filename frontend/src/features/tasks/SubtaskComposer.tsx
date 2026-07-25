import { type FormEvent, useRef, useState } from 'react'
import type { Task, TaskCreate, TaskPriority } from '../../types/task'
import { fireAndForget } from '../../utils/async'
import { parseDurationInput } from '../../utils/duration'

interface SubtaskComposerProps {
  parent: Task
  onCreate: (data: TaskCreate) => Promise<void>
  /** Hand off to the full task modal. Omitted where no modal exists (detail page). */
  onMoreOptions?: (defaults: Partial<TaskCreate>) => void
  onCancel: () => void
}

// Seed priority/due date from the parent as overridable starting values.
function draftFromParent(parent: Task) {
  return {
    title: '',
    priority: parent.priority,
    dueDate: parent.due_date ?? '',
    estimate: '',
  }
}

export function SubtaskComposer({
  parent,
  onCreate,
  onMoreOptions,
  onCancel,
}: SubtaskComposerProps) {
  const [draft, setDraft] = useState(() => draftFromParent(parent))
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  // State updates don't land until the next render, so two submit events fired
  // in the same tick would both pass a `saving` check. The ref closes that gap.
  const savingRef = useRef(false)

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (savingRef.current || !draft.title.trim()) return
    const estimatedMinutes = parseDurationInput(draft.estimate)
    if (estimatedMinutes === undefined) {
      setError('Use something like 30m, 2h, or 1 day')
      return
    }
    savingRef.current = true
    setSaving(true)
    try {
      await onCreate({
        title: draft.title.trim(),
        parent_task_id: parent.id,
        priority: draft.priority,
        due_date: draft.dueDate || null,
        estimated_minutes: estimatedMinutes,
      })
    } finally {
      // On success the parent unmounts this composer; on failure the draft
      // stays put and the controls come back so the user can retry.
      savingRef.current = false
      setSaving(false)
    }
  }

  // Hand the in-progress draft to the full task modal for the long-tail fields.
  function handleMoreOptions() {
    if (!onMoreOptions) return
    const estimatedMinutes = parseDurationInput(draft.estimate)
    onMoreOptions({
      parent_task_id: parent.id,
      title: draft.title.trim() || undefined,
      priority: draft.priority,
      due_date: draft.dueDate || null,
      // Drop an unparseable estimate rather than blocking the handoff; the modal
      // re-validates on save.
      estimated_minutes: estimatedMinutes === undefined ? null : estimatedMinutes,
    })
  }

  return (
    <form
      className="task-subtask-form"
      onSubmit={(e) => fireAndForget(handleSubmit(e))}
    >
      <input
        autoFocus
        value={draft.title}
        onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
        placeholder="Subtask title"
        disabled={saving}
      />
      <div className="task-subtask-fields">
        <label>
          <span>Priority</span>
          <select
            value={draft.priority}
            onChange={(e) =>
              setDraft((d) => ({
                ...d,
                priority: e.target.value as TaskPriority,
              }))
            }
            disabled={saving}
          >
            <option value="urgent">Urgent</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
        <label>
          <span>Due date</span>
          <input
            type="date"
            value={draft.dueDate}
            onChange={(e) => setDraft((d) => ({ ...d, dueDate: e.target.value }))}
            disabled={saving}
          />
        </label>
        <label>
          <span>Estimate</span>
          <input
            placeholder="30m, 2h, 1 day"
            value={draft.estimate}
            onChange={(e) => setDraft((d) => ({ ...d, estimate: e.target.value }))}
            disabled={saving}
          />
        </label>
      </div>
      {error && <p role="alert">{error}</p>}
      <div className="task-subtask-actions">
        <button type="submit" disabled={saving || !draft.title.trim()}>
          {saving ? 'Adding…' : 'Add'}
        </button>
        <button type="button" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
        {onMoreOptions && (
          <button
            type="button"
            className="secondary-action"
            onClick={handleMoreOptions}
            disabled={saving}
          >
            More options
          </button>
        )}
      </div>
    </form>
  )
}
