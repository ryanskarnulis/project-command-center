import type { Task } from '../../../types/task'
import { matchesFilters, selectedPriorities, type Filters, type SortMode } from '../taskFilters'
import { workflowLabel } from '../taskMeta'

export function matchingMobileTasks(tasks: Task[], filters: Filters, showDone: boolean): Task[] {
  return tasks.filter((task) => {
    if (filters.status === 'done') return task.workflow_status === 'done' && matchesFilters(task, filters)
    if (!showDone && task.workflow_status === 'done') return false
    return matchesFilters(task, filters)
  })
}

export const SORT_LABELS: Record<SortMode, string> = {
  smart: 'Smart order', due_date: 'Due date', priority: 'Priority', newest: 'Created', project: 'Project',
}

export interface FilterChip {
  label: string
  filters?: Filters
  sortMode?: SortMode
}

export function mobileFilterChips(filters: Filters, sortMode: SortMode): FilterChip[] {
  const chips: FilterChip[] = []
  if (filters.search.trim()) chips.push({ label: `Search: ${filters.search.trim()}`, filters: { ...filters, search: '' } })
  if (filters.overdue) chips.push({ label: 'Overdue', filters: { ...filters, overdue: false } })
  if (filters.dueSoon) chips.push({ label: 'Due soon', filters: { ...filters, dueSoon: false } })
  const priorities = selectedPriorities(filters)
  if (priorities.length) {
    const label = priorities.length === 2 && priorities.includes('urgent') && priorities.includes('high')
      ? 'High+' : priorities.map((p) => p[0].toUpperCase() + p.slice(1)).join(' + ')
    chips.push({ label: `Priority: ${label}`, filters: { ...filters, priority: '', priorities: undefined } })
  }
  if (filters.status) {
    const label = filters.status === 'blocked' ? 'Blocked' : filters.status === 'blocking' ? 'Blocking' : workflowLabel(filters.status)
    chips.push({ label: `Status: ${label}`, filters: { ...filters, status: '' } })
  }
  // Legacy deep links must never filter invisibly, even for controls not in the sheet.
  if (filters.projectId !== '') chips.push({ label: `Project: ${filters.projectId}`, filters: { ...filters, projectId: '' } })
  if (sortMode !== 'smart') chips.push({ label: `Sort: ${SORT_LABELS[sortMode]}`, sortMode: 'smart' })
  return chips
}
