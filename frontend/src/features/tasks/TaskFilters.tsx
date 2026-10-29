import { Search } from 'lucide-react'
import type { TaskPriority } from '../../types/task'
import {
  EMPTY_FILTERS,
  type Filters,
  type SortMode,
  type StatusView,
  type ViewMode,
} from './taskFilters'

interface TaskQueryUpdate {
  filters?: Filters
  sortMode?: SortMode
  view?: ViewMode
  addingTask?: boolean
}

interface TaskFiltersProps {
  filters: Filters
  sortMode: SortMode
  view: ViewMode
  filtersActive: boolean
  updateTaskQuery: (next: TaskQueryUpdate) => void
}

export function TaskFilters({
  filters,
  sortMode,
  view,
  filtersActive,
  updateTaskQuery,
}: TaskFiltersProps) {
  // One row on desktop: search, the selects, the two quick toggles, then
  // Clear. Each control names itself through aria-label, so there is no
  // visible label row or section heading spending height above the tasks.
  return (
    <div className="task-filters" role="search" aria-label="Filter tasks">
      <label className="task-search-field">
        <Search size={15} aria-hidden="true" />
        <input
          aria-label="Search tasks"
          value={filters.search}
          onChange={(e) =>
            updateTaskQuery({ filters: { ...filters, search: e.target.value } })
          }
          placeholder="Search tasks"
        />
      </label>

      <div className="task-filter-grid">
        {view !== 'board' && (
        <label>
          <select
            aria-label="Filter by status"
            value={filters.status}
            onChange={(e) =>
              updateTaskQuery({
                filters: { ...filters, status: e.target.value as StatusView },
              })
            }
          >
            <option value="">All statuses</option>
            <option value="open">Open</option>
            <option value="in_progress">In progress</option>
            <option value="blocking">Blocking</option>
            <option value="blocked">Blocked</option>
            <option value="done">Done</option>
          </select>
        </label>
        )}

        <label>
          <select
            aria-label="Filter by priority"
            value={filters.priorities?.length ? 'multiple' : filters.priority}
            onChange={(e) =>
              updateTaskQuery({
                filters: {
                  ...filters,
                  priority: e.target.value as TaskPriority | '',
                  priorities: undefined,
                },
              })
            }
          >
            <option value="">All priorities</option>
            {/* Display-only: shows a mobile multi-selection this single-select
                cannot represent. Disabled so it can never be chosen back, which
                would set `priority` to a value no Task ever has. */}
            {filters.priorities !== undefined && filters.priorities.length > 0 ? (
              <option value="multiple" disabled>
                {filters.priorities.join(' + ')}
              </option>
            ) : null}
            <option value="urgent">Urgent</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>

        {view !== 'board' && (
        <label>
          <select
            aria-label="Sort tasks"
            value={sortMode}
            onChange={(e) =>
              updateTaskQuery({ sortMode: e.target.value as SortMode })
            }
          >
            <option value="smart">Smart order</option>
            <option value="due_date">Due date</option>
            <option value="priority">Priority</option>
            <option value="project">Project</option>
            <option value="newest">Newest</option>
          </select>
        </label>
        )}
      </div>

      <div className="task-filter-toggles" aria-label="Quick filters">
        <label className={filters.overdue ? 'selected' : ''}>
          <input
            type="checkbox"
            checked={filters.overdue}
            onChange={(e) =>
              updateTaskQuery({
                filters: { ...filters, overdue: e.target.checked },
              })
            }
          />
          Overdue
        </label>

        <label className={filters.dueSoon ? 'selected' : ''}>
          <input
            type="checkbox"
            checked={filters.dueSoon}
            onChange={(e) =>
              updateTaskQuery({
                filters: { ...filters, dueSoon: e.target.checked },
              })
            }
          />
          Due soon
        </label>
      </div>
      {filtersActive && (
        <button
          type="button"
          className="task-filters-clear"
          onClick={() => updateTaskQuery({ filters: EMPTY_FILTERS })}
        >
          Clear filters
        </button>
      )}
    </div>
  )
}
