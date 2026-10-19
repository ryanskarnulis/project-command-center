import { useState } from 'react'
import { Search } from 'lucide-react'
import {
  EMPTY_TRASH_FILTERS,
  matchingTrashRows,
  pluralize,
  type TrashFilters,
  type TrashRow,
  type TrashTypeFilter,
} from './trashMeta'

const TYPE_OPTIONS: [TrashTypeFilter, string][] = [
  ['all', 'All'],
  ['projects', 'Projects'],
  ['tasks', 'Tasks'],
]

interface TrashFilterFormProps {
  filters: TrashFilters
  rows: { projects: TrashRow[]; tasks: TrashRow[] }
  /** The heading's id, for the surrounding dialog's `aria-labelledby`. */
  titleId: string
  onApply: (next: TrashFilters) => void
  /** Desktop opens the panel with the cursor already in the search field. */
  autoFocusSearch?: boolean
}

/**
 * The M03f filter form reduced to what /trash filters on: a search field and a
 * single-select type row. Pending until Apply (or Enter); the phone hosts it in
 * a bottom sheet, desktop in a panel anchored to the filter control.
 */
export function TrashFilterForm({ filters, rows, titleId, onApply, autoFocusSearch = false }: TrashFilterFormProps) {
  const [pending, setPending] = useState(filters)
  const matching = matchingTrashRows(rows, pending)
  const count = matching.projects.length + matching.tasks.length

  return (
    <form
      className="trash-filter-form"
      onSubmit={(event) => {
        event.preventDefault()
        onApply(pending)
      }}
    >
      <header>
        <h2 id={titleId}>Filters</h2>
        <button type="button" className="task-sheet-reset" onClick={() => setPending(EMPTY_TRASH_FILTERS)}>Reset</button>
      </header>
      <label className="task-sheet-search">
        <Search size={15} aria-hidden="true" />
        <input
          aria-label="Search trash"
          placeholder="Project or task"
          value={pending.search}
          autoFocus={autoFocusSearch}
          onChange={(event) => setPending({ ...pending, search: event.target.value })}
        />
      </label>
      <fieldset>
        <legend>Type</legend>
        <div className="task-sheet-options">
          {TYPE_OPTIONS.map(([value, label]) => (
            <button type="button" key={value} aria-pressed={pending.type === value} onClick={() => setPending({ ...pending, type: value })}>{label}</button>
          ))}
        </div>
      </fieldset>
      <button type="submit" className="task-sheet-apply">
        Show {pluralize(count, 'item')}
      </button>
    </form>
  )
}
