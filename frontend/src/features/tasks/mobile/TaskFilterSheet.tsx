import { useState } from 'react'
import { Search } from 'lucide-react'
import { BottomSheet } from '../../../components/BottomSheet'
import type { Task, TaskPriority } from '../../../types/task'
import { EMPTY_FILTERS, selectedPriorities, type Filters, type SortMode, type StatusView } from '../taskFilters'
import { matchingMobileTasks, SORT_LABELS } from './mobileTaskFilters'

interface Props {
  filters: Filters
  sortMode: SortMode
  tasks: Task[]
  showDone: boolean
  loading: boolean
  error: string | null
  onClose: () => void
  onApply: (next: { filters: Filters; sortMode: SortMode }) => void
}

export function TaskFilterSheet({ filters, sortMode, tasks, showDone, loading, error, onClose, onApply }: Props) {
  const [pending, setPending] = useState(filters)
  const [sort, setSort] = useState(sortMode)
  const count = matchingMobileTasks(tasks, pending, showDone).length
  const priorities = selectedPriorities(pending)

  function togglePriority(priority: TaskPriority): void {
    const next = priorities.includes(priority) ? priorities.filter((p) => p !== priority) : [...priorities, priority]
    setPending({ ...pending, priority: next.length === 1 ? next[0] : '', priorities: next.length > 1 ? next : undefined })
  }

  return (
    <BottomSheet className="task-filter-sheet" labelledBy="task-filter-sheet-title" handleLabel="Close filters" onClose={onClose}>
      <header>
        <h2 id="task-filter-sheet-title">Filters</h2>
        <button type="button" className="task-sheet-reset" onClick={() => { setPending(EMPTY_FILTERS); setSort('smart') }}>Reset</button>
      </header>
      <label className="task-sheet-search">
        <Search size={15} aria-hidden="true" />
        <input aria-label="Search project tasks" placeholder="Title or description" value={pending.search} onChange={(event) => setPending({ ...pending, search: event.target.value })} />
      </label>
      <fieldset>
        <legend>Status</legend>
        <div className="task-sheet-options">
          {([['', 'All'], ['open', 'Open'], ['in_progress', 'In progress'], ['done', 'Done'], ...(pending.status === 'blocked' || pending.status === 'blocking' ? [[pending.status, pending.status === 'blocked' ? 'Blocked' : 'Blocking']] : [])] as [StatusView, string][]).map(([value, label]) => (
            <button type="button" key={value} aria-pressed={pending.status === value} onClick={() => setPending({ ...pending, status: value })}>{label}</button>
          ))}
        </div>
      </fieldset>
      <fieldset>
        <legend>Priority</legend>
        <div className="task-sheet-options">
          <button type="button" aria-pressed={!priorities.length} onClick={() => setPending({ ...pending, priority: '', priorities: undefined })}>All</button>
          {(['urgent', 'high', 'medium', 'low'] as const).map((priority) => (
            <button type="button" key={priority} aria-pressed={priorities.includes(priority)} onClick={() => togglePriority(priority)}>{priority[0].toUpperCase() + priority.slice(1)}</button>
          ))}
        </div>
      </fieldset>
      <fieldset>
        <legend>Sort</legend>
        <div className="task-sheet-options">
          {(['smart', 'due_date', 'priority', 'newest', ...(sort === 'project' ? ['project'] as const : [])] as const).map((value) => (
            <button type="button" key={value} aria-pressed={sort === value} onClick={() => setSort(value)}>{SORT_LABELS[value]}</button>
          ))}
        </div>
      </fieldset>
      <div className="task-sheet-toggles">
        {([['overdue', 'Overdue'], ['dueSoon', 'Due soon']] as const).map(([key, label]) => (
          <button type="button" role="switch" aria-checked={pending[key]} key={key} onClick={() => setPending({ ...pending, [key]: !pending[key] })}>
            <span>{label}</span><span className="task-sheet-switch" aria-hidden="true" />
          </button>
        ))}
      </div>
      {error && <p role="alert">{error}</p>}
      <button type="button" className="task-sheet-apply" disabled={loading || !!error} onClick={() => onApply({ filters: pending, sortMode: sort })}>
        {loading ? 'Loading tasks…' : `Show ${count} ${count === 1 ? 'task' : 'tasks'}`}
      </button>
    </BottomSheet>
  )
}
