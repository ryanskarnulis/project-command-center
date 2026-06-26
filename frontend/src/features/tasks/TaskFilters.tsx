import { Search, SlidersHorizontal } from 'lucide-react'
import type { Project } from '../../types/project'
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
  isGlobal: boolean
  projects: Project[]
  filtersActive: boolean
  activeFilterCount: number
  updateTaskQuery: (next: TaskQueryUpdate) => void
}

export function TaskFilters({
  filters,
  sortMode,
  view,
  isGlobal,
  projects,
  filtersActive,
  activeFilterCount,
  updateTaskQuery,
}: TaskFiltersProps) {
  return (
    <div className="task-filters" role="search" aria-label="Filter tasks">
      <div className="task-filters-header">
        <div className="task-filters-title">
          <SlidersHorizontal size={17} aria-hidden="true" />
          <strong>Filters</strong>
          {activeFilterCount > 0 && (
            <span className="count-badge">{activeFilterCount} active</span>
          )}
        </div>
        {filtersActive && (
          <button
            type="button"
            className="secondary-action"
            onClick={() => updateTaskQuery({ filters: EMPTY_FILTERS })}
          >
            Clear filters
          </button>
        )}
      </div>

      <label className="task-search-field">
        <span>Search</span>
        <div>
          <Search size={17} aria-hidden="true" />
          <input
            aria-label="Search tasks"
            value={filters.search}
            onChange={(e) =>
              updateTaskQuery({ filters: { ...filters, search: e.target.value } })
            }
            placeholder="Title or description"
          />
        </div>
      </label>

      <div className="task-filter-grid">
        {view !== 'board' && (
        <label>
          <span>Status</span>
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
          <span>Priority</span>
          <select
            aria-label="Filter by priority"
            value={filters.priority}
            onChange={(e) =>
              updateTaskQuery({
                filters: {
                  ...filters,
                  priority: e.target.value as TaskPriority | '',
                },
              })
            }
          >
            <option value="">All priorities</option>
            <option value="urgent">Urgent</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>

        {isGlobal && (
          <label>
            <span>Project</span>
            <select
              aria-label="Filter by project"
              value={filters.projectId === '' ? '' : String(filters.projectId)}
              onChange={(e) =>
                updateTaskQuery({
                  filters: {
                    ...filters,
                    projectId:
                      e.target.value === '' ? '' : Number(e.target.value),
                  },
                })
              }
            >
              <option value="">All projects</option>
              {projects.map((p) => (
                <option key={p.id} value={String(p.id)}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        )}

        {view !== 'board' && (
        <label>
          <span>Sort</span>
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
    </div>
  )
}
