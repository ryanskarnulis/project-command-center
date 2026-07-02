import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  filtersFromParams,
  isActive,
  isTruthyParam,
  paramsFromState,
  sortFromParams,
  viewFromParams,
  type Filters,
  type SortMode,
  type ViewMode,
} from './taskFilters'

interface TaskQueryUpdate {
  filters?: Filters
  sortMode?: SortMode
  view?: ViewMode
  addingTask?: boolean
}

interface UseTaskUrlState {
  view: ViewMode
  addingTask: boolean
  filters: Filters
  sortMode: SortMode
  filtersActive: boolean
  hasNonStatusFilters: boolean
  activeFilterCount: number
  updateTaskQuery: (next: TaskQueryUpdate) => void
  selectView: (next: ViewMode) => void
}

// Board/list, filters, sorting, and the create-modal deep link are URL-backed
// so links, refreshes, and browser back/forward restore the same task view.
export function useTaskUrlState(): UseTaskUrlState {
  const [searchParams, setSearchParams] = useSearchParams()
  const view = viewFromParams(searchParams)
  const addingTask = isTruthyParam(searchParams.get('new'))
  const filters = useMemo(() => filtersFromParams(searchParams), [searchParams])
  const sortMode = sortFromParams(searchParams)

  function updateTaskQuery(next: TaskQueryUpdate) {
    const params = paramsFromState(
      next.filters ?? filters,
      next.sortMode ?? sortMode,
      next.view ?? view,
      next.addingTask ?? addingTask,
    )
    // paramsFromState rebuilds the query from scratch; carry the peek-panel
    // param so changing a filter or view doesn't close an open panel.
    const openTask = searchParams.get('task')
    if (openTask !== null) params.set('task', openTask)
    setSearchParams(params, { replace: true })
  }

  function selectView(next: ViewMode) {
    updateTaskQuery({ view: next })
  }

  const filtersActive = isActive(filters)
  const hasNonStatusFilters =
    filters.search.trim() !== '' ||
    filters.priority !== '' ||
    filters.projectId !== '' ||
    filters.overdue ||
    filters.dueSoon
  const activeFilterCount = [
    filters.search.trim() !== '',
    filters.status !== '',
    filters.priority !== '',
    filters.projectId !== '',
    filters.overdue,
    filters.dueSoon,
  ].filter(Boolean).length

  return {
    view,
    addingTask,
    filters,
    sortMode,
    filtersActive,
    hasNonStatusFilters,
    activeFilterCount,
    updateTaskQuery,
    selectView,
  }
}
