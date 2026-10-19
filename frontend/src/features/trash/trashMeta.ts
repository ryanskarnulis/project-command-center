import type { Trash } from '../../types/trash'
import { formatRelative } from '../../utils/dates'
import type { TrashKind } from './useTrash'

/* M08f — the /trash row model and its meta line, shared by both trees.
 *
 * A trash row is not a TaskCard or a ProjectCard: nothing on a deleted item
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
  /**
   * When the retention clock started (#321): the row's own `deletedAt`, or — for
   * a task whose project is also in the trash and was deleted earlier — the
   * project's, because the server purges an expired project with every trashed
   * task it owns, exactly as Delete forever on the project row would.
   */
  expiresFrom: string | null
}

export const TRASH_GROUPS: { kind: TrashKind; label: string }[] = [
  { kind: 'projects', label: 'Projects' },
  { kind: 'tasks', label: 'Tasks' },
]

export function rowNoun(row: Pick<TrashRow, 'kind'>): 'project' | 'task' {
  return row.kind === 'projects' ? 'project' : 'task'
}

/** One key per row across both kinds — selection mode spans them. */
export function rowKey(row: Pick<TrashRow, 'kind' | 'id'>): string {
  return `${row.kind}:${row.id}`
}

export function trashRows(trash: Trash): { projects: TrashRow[]; tasks: TrashRow[] } {
  const trashedProjects = new Map(trash.projects.map((project) => [project.id, project]))
  return {
    projects: trash.projects.map((project) => ({
      kind: 'projects',
      id: project.id,
      title: project.name,
      deletedAt: project.deleted_at ?? null,
      archivedTaskCount: project.archived_task_count,
      purgeTaskCount: project.purge_task_count,
      projectName: null,
      expiresFrom: project.deleted_at ?? null,
    })),
    tasks: trash.tasks.map((task) => {
      const project = task.project_id !== null ? trashedProjects.get(task.project_id) : undefined
      return {
        kind: 'tasks',
        id: task.id,
        title: task.title,
        deletedAt: task.deleted_at ?? null,
        archivedTaskCount: 0,
        purgeTaskCount: 0,
        projectName: project?.name ?? null,
        expiresFrom: earliest(task.deleted_at ?? null, project?.deleted_at ?? null),
      }
    }),
  }
}

function earliest(a: string | null, b: string | null): string | null {
  if (a === null) return b
  if (b === null) return a
  return Date.parse(b) < Date.parse(a) ? b : a
}

export function pluralize(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

/**
 * The row's meta line: at most two facts, in a fixed order. `Deleted {relative}`
 * always leads; the second is what a restore brings with it (projects) or where
 * the task belonged (tasks whose project is also in the trash). The retention
 * countdown the handoff draws third is `retentionMeta`, kept apart because it
 * is an exception, colored as a warning, not a fact every row carries.
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

const DAY_MS = 86_400_000

/** Rows this close to their purge say so; earlier than that the sub-line's
 * `removed after n days` covers them (exceptions-only meta). */
export const RETENTION_WARN_DAYS = 7

/**
 * Whole days until the server purges this row (#321): a part day counts as a
 * day, so `1 day left` means "gone within a day", and 0 means the row has
 * expired and goes on the job's next pass. `null` when retention is off
 * (`retentionDays` 0) or the row has no deletion stamp.
 */
export function retentionDaysLeft(row: Pick<TrashRow, 'expiresFrom'>, retentionDays: number, now: number = Date.now()): number | null {
  if (retentionDays <= 0 || !row.expiresFrom) return null
  const remaining = Date.parse(row.expiresFrom) + retentionDays * DAY_MS - now
  return Math.max(0, Math.ceil(remaining / DAY_MS))
}

/** The countdown fact — only inside the last `RETENTION_WARN_DAYS`, else `null`. */
export function retentionMeta(row: Pick<TrashRow, 'expiresFrom'>, retentionDays: number, now: number = Date.now()): string | null {
  const left = retentionDaysLeft(row, retentionDays, now)
  if (left === null || left > RETENTION_WARN_DAYS) return null
  return left === 0 ? 'Removed today' : `${pluralize(left, 'day')} left`
}

/** The heading sub-line's retention fact, or `null` when retention is off. */
export function retentionLine(retentionDays: number): string | null {
  return retentionDays > 0 ? `removed after ${pluralize(retentionDays, 'day')}` : null
}

/** Search and type are both client-side. */
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
 * The purge confirm for a selection that can span both kinds. The total counts every row the purge destroys,
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
