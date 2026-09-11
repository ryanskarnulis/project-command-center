import type { Trash } from '../../../types/trash'
import { formatRelative } from '../../../utils/dates'
import type { TrashKind } from '../useTrash'

/* M08f — the mobile /trash row model and its meta line.
 *
 * The mobile row is not a TaskCard or a ProjectCard: nothing on a deleted item
 * is being triaged, so status, priority, due date, estimate and repeat all go.
 * What is left is the one fact that helps you find what you deleted (when) and
 * at most one more that tells you what restoring it does. */

export type TrashTypeFilter = 'all' | 'projects' | 'tasks'

export interface TrashFilters {
  search: string
  type: TrashTypeFilter
}

export const EMPTY_TRASH_FILTERS: TrashFilters = { search: '', type: 'all' }

export interface TrashRow {
  kind: TrashKind
  id: number
  title: string
  deletedAt: string | null
  /** Projects: tasks cascade-trashed with it, which a plain Restore brings back. */
  archivedTaskCount: number
  /** Projects: every trashed task it owns — what a purge destroys. */
  purgeTaskCount: number
  /** Tasks: the name of its project when that project is itself in the trash. */
  projectName: string | null
}

/** One key per row across both kinds — selection mode spans them. */
export function rowKey(row: Pick<TrashRow, 'kind' | 'id'>): string {
  return `${row.kind}:${row.id}`
}

export function trashRows(trash: Trash): { projects: TrashRow[]; tasks: TrashRow[] } {
  const trashedProjects = new Map(trash.projects.map((project) => [project.id, project.name]))
  return {
    projects: trash.projects.map((project) => ({
      kind: 'projects',
      id: project.id,
      title: project.name,
      deletedAt: project.deleted_at ?? null,
      archivedTaskCount: project.archived_task_count,
      purgeTaskCount: project.purge_task_count,
      projectName: null,
    })),
    tasks: trash.tasks.map((task) => ({
      kind: 'tasks',
      id: task.id,
      title: task.title,
      deletedAt: task.deleted_at ?? null,
      archivedTaskCount: 0,
      purgeTaskCount: 0,
      projectName: task.project_id !== null ? (trashedProjects.get(task.project_id) ?? null) : null,
    })),
  }
}

export function pluralize(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

/**
 * The row's meta line: at most two facts, in a fixed order. `Deleted {relative}`
 * always leads; the second is what a restore brings with it (projects) or where
 * the task belonged (tasks whose project is also in the trash). The retention
 * countdown the handoff draws third needs a purge policy the backend does not
 * have, so it is not emitted — see the README's open questions.
 */
export function deletedMeta(row: TrashRow, now: number = Date.now()): string[] {
  const facts: string[] = []
  if (row.deletedAt) facts.push(`Deleted ${formatRelative(row.deletedAt, now)}`)
  if (row.kind === 'projects' && row.archivedTaskCount > 0) {
    facts.push(`${pluralize(row.archivedTaskCount, 'task')} restore${row.archivedTaskCount === 1 ? 's' : ''} with it`)
  } else if (row.kind === 'tasks' && row.projectName) {
    facts.push(row.projectName)
  }
  return facts.slice(0, 2)
}

/** Search and type are both client-side, exactly as on desktop. */
export function matchingTrashRows(
  rows: { projects: TrashRow[]; tasks: TrashRow[] },
  filters: TrashFilters,
): { projects: TrashRow[]; tasks: TrashRow[] } {
  const query = filters.search.trim().toLowerCase()
  const match = (row: TrashRow) => query === '' || row.title.toLowerCase().includes(query)
  return {
    projects: filters.type === 'tasks' ? [] : rows.projects.filter(match),
    tasks: filters.type === 'projects' ? [] : rows.tasks.filter(match),
  }
}

export function trashFiltersActive(filters: TrashFilters): boolean {
  return filters.search.trim() !== '' || filters.type !== 'all'
}

export interface TrashFilterChip {
  label: string
  /** The filters with this one chip removed. */
  next: TrashFilters
}

/** The chips row mirrors the applied filters one-to-one; the list is never filtered without it. */
export function trashFilterChips(filters: TrashFilters): TrashFilterChip[] {
  const chips: TrashFilterChip[] = []
  if (filters.type !== 'all') {
    chips.push({ label: filters.type === 'projects' ? 'Projects' : 'Tasks', next: { ...filters, type: 'all' } })
  }
  if (filters.search.trim() !== '') {
    chips.push({ label: `“${filters.search.trim()}”`, next: { ...filters, search: '' } })
  }
  return chips
}

/**
 * The purge confirm for a selection that can span both kinds. Same shape as
 * the desktop bulk confirm — the total counts every row the purge destroys,
 * including the trashed tasks a selected project owns — with the kinds spelled
 * out whenever the total is more than the bare selection.
 */
export function purgeSelectionScope(rows: TrashRow[]): string {
  const projects = rows.filter((row) => row.kind === 'projects')
  const tasks = rows.filter((row) => row.kind === 'tasks')
  const cascade = projects.reduce((sum, row) => sum + row.purgeTaskCount, 0)
  const total = rows.length + cascade
  const parts: string[] = []
  if (projects.length) parts.push(pluralize(projects.length, 'project'))
  if (tasks.length) parts.push(pluralize(tasks.length, 'task'))
  if (cascade) parts.push(`${pluralize(cascade, 'trashed task')} ${projects.length === 1 ? 'it owns' : 'they own'}`)
  const detail = parts.length > 1 ? ` (${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]})` : ''
  return `${pluralize(total, 'item')}${detail}`
}
