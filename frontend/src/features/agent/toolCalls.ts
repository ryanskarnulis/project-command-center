// Presentation + undo mapping for the tool calls persisted on an assistant
// message. Pure functions over ToolCallRecord so they're testable without DOM.

import { deleteProject, restoreProject } from '../../api/projects'
import {
  deleteTask,
  markTaskDone,
  reopenTask,
  restoreTask,
} from '../../api/tasks'
import type { ToolCallRecord } from '../../types/agent'

/** An inverse action for one successful tool call ("agent created task X —
 * undo" → trash it; "agent trashed task X — undo" → restore it). */
export interface UndoAction {
  /** Button label, e.g. "Undo (move to trash)". */
  label: string
  perform: () => Promise<unknown>
}

// The agent returns read models as JSON strings; mutating calls carry the ids
// we need either there or in their arguments. Parse defensively — a record we
// can't read simply gets no summary enrichment / no undo.
function parseResult(record: ToolCallRecord): Record<string, unknown> | null {
  if (record.result === null) return null
  try {
    const parsed: unknown = JSON.parse(record.result)
    return typeof parsed === 'object' && parsed !== null
      ? (parsed as Record<string, unknown>)
      : null
  } catch {
    return null
  }
}

function resultId(record: ToolCallRecord): number | null {
  const id = parseResult(record)?.id
  return typeof id === 'number' ? id : null
}

function argNumber(record: ToolCallRecord, key: string): number | null {
  const value = record.arguments[key]
  return typeof value === 'number' ? value : null
}

function resultString(record: ToolCallRecord, key: string): string | null {
  const value = parseResult(record)?.[key]
  return typeof value === 'string' ? value : null
}

function argString(record: ToolCallRecord, path: [string, string?]): string | null {
  const [head, tail] = path
  const value = record.arguments[head]
  if (tail === undefined) return typeof value === 'string' ? value : null
  if (typeof value !== 'object' || value === null) return null
  const nested = (value as Record<string, unknown>)[tail]
  return typeof nested === 'string' ? nested : null
}

/** The entity name a call touched, for the human summary. Falls back through
 * result payload → arguments → null. */
function subjectName(record: ToolCallRecord): string | null {
  return (
    resultString(record, 'title') ??
    resultString(record, 'name') ??
    argString(record, ['data', 'title']) ??
    argString(record, ['data', 'name']) ??
    null
  )
}

const quoted = (name: string | null) => (name === null ? '' : ` “${name}”`)

/** One human line summarizing a tool call, e.g. `Created task “Fix VPN”`. */
export function describeToolCall(record: ToolCallRecord): string {
  const name = subjectName(record)
  switch (record.tool) {
    case 'create_task':
      return `Created task${quoted(name)}`
    case 'update_task':
      return `Updated task${quoted(name)}`
    case 'complete_task':
      return `Completed task${quoted(name)}`
    case 'reopen_task':
      return `Reopened task${quoted(name)}`
    case 'trash_task':
      return 'Moved a task to the trash'
    case 'restore_task':
      return `Restored task${quoted(name)}`
    case 'create_project':
      return `Created project${quoted(name)}`
    case 'update_project':
      return `Updated project${quoted(name)}`
    case 'close_project':
      return `Closed project${quoted(name)}`
    case 'reopen_project':
      return `Reopened project${quoted(name)}`
    case 'trash_project':
      return 'Moved a project to the trash'
    case 'restore_project':
      return 'Restored a project'
    case 'add_dependency':
      return 'Added a dependency'
    case 'remove_dependency':
      return 'Removed a dependency'
    case 'skip_occurrence':
      return 'Skipped a recurring occurrence'
    case 'stop_recurrence':
      return `Stopped recurrence on${quoted(name) || ' a task'}`
    case 'search':
      return `Searched for${quoted(argString(record, ['query'])) || ' something'}`
    case 'list_tasks':
      return 'Looked at tasks'
    case 'get_task':
      return 'Looked at a task'
    case 'list_projects':
      return 'Looked at projects'
    case 'get_project':
      return 'Looked at a project'
    case 'list_dependencies':
      return 'Looked at dependencies'
    case 'get_focus_plan':
      return 'Looked at the focus plan'
    case 'list_trash':
      return 'Looked in the trash'
    case 'list_activity':
      return 'Looked at project activity'
    default:
      return record.tool.replaceAll('_', ' ')
  }
}

/** True for calls that changed something (and so deserve prominence). */
export function isMutation(record: ToolCallRecord): boolean {
  return !/^(list_|get_|search$)/.test(record.tool)
}

/**
 * The inverse of a successful mutating call, when one exists. Everything here
 * routes through the same REST endpoints the rest of the UI uses (service
 * layer underneath), so undo is itself audited and — for deletes — a
 * restorable soft delete. Calls whose inverse would need prior state
 * (update_task, update_project, dependency edits) get no undo.
 */
export function undoFor(record: ToolCallRecord): UndoAction | null {
  if (record.error !== null) return null
  switch (record.tool) {
    case 'create_task': {
      const id = resultId(record)
      return id === null
        ? null
        : { label: 'Undo (move to trash)', perform: () => deleteTask(id) }
    }
    case 'create_project': {
      const id = resultId(record)
      return id === null
        ? null
        : { label: 'Undo (move to trash)', perform: () => deleteProject(id) }
    }
    case 'complete_task': {
      const id = resultId(record)
      return id === null
        ? null
        : { label: 'Undo (reopen)', perform: () => reopenTask(id) }
    }
    case 'reopen_task': {
      const id = resultId(record)
      return id === null
        ? null
        : { label: 'Undo (mark done)', perform: () => markTaskDone(id) }
    }
    case 'trash_task': {
      // trash_task returns a plain-text confirmation; the id is an argument.
      const id = argNumber(record, 'task_id')
      return id === null
        ? null
        : { label: 'Undo (restore)', perform: () => restoreTask(id) }
    }
    case 'trash_project': {
      const id = argNumber(record, 'project_id')
      return id === null
        ? null
        : {
            label: 'Undo (restore)',
            perform: () => restoreProject(id, true),
          }
    }
    default:
      return null
  }
}
