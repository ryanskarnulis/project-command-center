import { useRef, useState } from 'react'
import { updateTask } from '../../api/tasks'
import type { EditScope, Task, TaskUpdate } from '../../types/task'

export type SaveState = 'idle' | 'saving' | 'saved' | 'error'

// Field edits that can sensibly cascade to future occurrences of a recurring
// task; changing one of these on a series prompts the edit-scope choice. A
// due-date or workflow change is inherently per-occurrence and never prompts.
const SCOPABLE_FIELDS: (keyof TaskUpdate)[] = [
  'title',
  'description',
  'priority',
  'estimated_minutes',
  'repeat_interval',
]

export interface ScopedTaskUpdate {
  saveState: SaveState
  saveError: string | null
  /** For page-level async work (delete, subtasks, …) that shares the save line. */
  setSaveState: (state: SaveState) => void
  setSaveError: (message: string | null) => void
  /** PATCH the task; scopable edits on a recurring task prompt for scope first. */
  savePatch: (patch: TaskUpdate) => void
  scopePromptOpen: boolean
  resolveScope: (scope: EditScope) => void
  cancelScope: () => void
  /** Local validation failure (bad estimate, empty title) on the save line. */
  reportError: (message: string) => void
}

/**
 * The task PATCH path shared by the detail view and the metadata chips:
 * applies a `TaskUpdate`, tracking save state, and parks scopable edits to a
 * recurring task until the user picks this-vs-future in EditScopeModal.
 */
export function useScopedTaskUpdate(
  task: Task | null,
  onSaved: (updated: Task) => void,
): ScopedTaskUpdate {
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [saveError, setSaveError] = useState<string | null>(null)
  // A scopable edit to a recurring task is parked here until the user picks a
  // scope in EditScopeModal; choosing replays it with the chosen edit_scope.
  const [pendingScopePatch, setPendingScopePatch] = useState<TaskUpdate | null>(null)
  // Monotonic id of the most recently *started* PATCH. Overlapping inline edits
  // can resolve out of order; only the newest write may publish its snapshot or
  // its save state, so a slow older response can never revert newer fields.
  const latestRequestId = useRef(0)

  async function applyPatch(patch: TaskUpdate) {
    if (!task) return
    const requestId = ++latestRequestId.current
    setSaveState('saving')
    setSaveError(null)
    try {
      const updated = await updateTask(task.id, patch)
      if (requestId !== latestRequestId.current) return
      onSaved(updated)
      setSaveState('saved')
    } catch (e: unknown) {
      if (requestId !== latestRequestId.current) return
      setSaveState('error')
      setSaveError(e instanceof Error ? e.message : 'Failed to save task')
    }
  }

  function savePatch(patch: TaskUpdate) {
    const isScopable = Object.keys(patch).some((key) =>
      SCOPABLE_FIELDS.includes(key as keyof TaskUpdate),
    )
    if (task?.recurrence_id && isScopable) {
      setPendingScopePatch(patch)
      return
    }
    void applyPatch(patch)
  }

  function resolveScope(scope: EditScope) {
    const patch = pendingScopePatch
    setPendingScopePatch(null)
    if (patch) void applyPatch({ ...patch, edit_scope: scope })
  }

  function reportError(message: string) {
    setSaveState('error')
    setSaveError(message)
  }

  return {
    saveState,
    saveError,
    setSaveState,
    setSaveError,
    savePatch,
    scopePromptOpen: pendingScopePatch !== null,
    resolveScope,
    cancelScope: () => setPendingScopePatch(null),
    reportError,
  }
}
