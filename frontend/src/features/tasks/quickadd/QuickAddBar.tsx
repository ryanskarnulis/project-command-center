import { type FormEvent, useMemo, useState } from 'react'
import type { Project } from '../../../types/project'
import type { TaskCreate, TaskPriority } from '../../../types/task'
import { DueDateChip } from '../chips/DueDateChip'
import { EstimateChip } from '../chips/EstimateChip'
import { PriorityChip } from '../chips/PriorityChip'
import { ProjectChip } from '../chips/ProjectChip'
import { parseQuickAdd } from './parseQuickAdd'

/** Chip edits that beat parsed tokens until the draft is submitted or cleared. */
interface Overrides {
  priority?: TaskPriority
  dueDate?: string | null
  projectId?: number | null
  estimatedMinutes?: number | null
}

interface Props {
  projects: Project[]
  /** Project-page scope: drafts without a #project token default here. */
  scopeProjectId?: number
  onCreate: (data: TaskCreate) => Promise<void>
  /** Hand the draft to the full task modal for the long-tail fields. */
  onMoreOptions: (defaults: Partial<TaskCreate>) => void
}

/**
 * Permanent one-line task input: tokens parse deterministically as you type
 * (see `parseQuickAdd`) into a live chip preview under the input. The chips
 * are the slice-1 editors, so a wrong guess is one click to fix — and a chip
 * edit sticks even if the token text disagrees.
 */
export function QuickAddBar({ projects, scopeProjectId, onCreate, onMoreOptions }: Props) {
  const [text, setText] = useState('')
  const [overrides, setOverrides] = useState<Overrides>({})
  const [saving, setSaving] = useState(false)

  const parsed = useMemo(() => parseQuickAdd(text, projects), [text, projects])

  const priority = overrides.priority ?? parsed.priority ?? 'medium'
  const dueDate = overrides.dueDate !== undefined ? overrides.dueDate : parsed.dueDate
  const projectId =
    overrides.projectId !== undefined
      ? overrides.projectId
      : parsed.projectId ?? scopeProjectId ?? null
  const estimatedMinutes =
    overrides.estimatedMinutes !== undefined
      ? overrides.estimatedMinutes
      : parsed.estimatedMinutes

  function buildDraft(): TaskCreate {
    return {
      title: parsed.title,
      priority,
      due_date: dueDate,
      estimated_minutes: estimatedMinutes,
      // Omitted (not null) when unset so the backend files it in General.
      ...(projectId !== null ? { project_id: projectId } : {}),
    }
  }

  function reset() {
    setText('')
    setOverrides({})
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (parsed.title === '' || saving) return
    setSaving(true)
    try {
      await onCreate(buildDraft())
      reset()
    } finally {
      setSaving(false)
    }
  }

  // The modal takes over the draft; title may still be empty (modal requires one).
  function handleMoreOptions() {
    onMoreOptions({
      title: parsed.title || undefined,
      priority,
      due_date: dueDate,
      estimated_minutes: estimatedMinutes,
      ...(projectId !== null ? { project_id: projectId } : {}),
    })
    reset()
  }

  return (
    <form className="quick-add" onSubmit={(e) => void handleSubmit(e)}>
      <div className="quick-add-row">
        <input
          aria-label="Quick add task"
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            // A cleared input is a fresh draft; stale chip edits shouldn't linger.
            if (e.target.value.trim() === '') setOverrides({})
          }}
          placeholder='Add a task — try "Renew TLS cert fri !high #ops ~20m"'
        />
        <button type="submit" disabled={saving || parsed.title === ''}>
          Add
        </button>
        <button type="button" className="secondary-action" onClick={handleMoreOptions}>
          More options
        </button>
      </div>
      {text.trim() !== '' && (
        <div className="quick-add-preview">
          <PriorityChip
            value={priority}
            onChange={(next) => setOverrides((o) => ({ ...o, priority: next }))}
          />
          <DueDateChip
            value={dueDate}
            onChange={(next) => setOverrides((o) => ({ ...o, dueDate: next }))}
          />
          <ProjectChip
            value={projectId}
            projects={projects}
            onChange={(next) => setOverrides((o) => ({ ...o, projectId: next }))}
          />
          <EstimateChip
            value={estimatedMinutes}
            onChange={(next) => setOverrides((o) => ({ ...o, estimatedMinutes: next }))}
          />
        </div>
      )}
    </form>
  )
}
