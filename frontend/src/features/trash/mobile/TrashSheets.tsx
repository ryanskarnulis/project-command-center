import { useState } from 'react'
import { Search } from 'lucide-react'
import { BottomSheet } from '../../../components/BottomSheet'
import { formatRelative } from '../../../utils/dates'
import {
  EMPTY_TRASH_FILTERS,
  matchingTrashRows,
  pluralize,
  type TrashFilters,
  type TrashRow,
  type TrashTypeFilter,
} from './trashMeta'

/* The two M08f bottom sheets, on the shared `BottomSheet` primitive. */

interface RowSheetProps {
  row: TrashRow
  onRestore: () => void
  /** Projects only: the `restoreSubtasks` choice desktop asks as a window.confirm. */
  onRestoreWithoutTasks: () => void
  onPurge: () => void
  onClose: () => void
}

/**
 * The row's ⋯ sheet — the complete, accessible path to everything the ring and
 * the swipe do, plus the one verb neither of them is allowed to carry.
 */
export function TrashRowSheet({ row, onRestore, onRestoreWithoutTasks, onPurge, onClose }: RowSheetProps) {
  return (
    <BottomSheet className="focus-sheet focus-row-sheet trash-row-sheet" labelledBy="trash-row-sheet-title" handleLabel="Close item actions" onClose={onClose}>
      <header className="focus-sheet-head focus-row-sheet-head">
        <h2 id="trash-row-sheet-title">{row.title}</h2>
        {row.deletedAt && <span>Deleted {formatRelative(row.deletedAt)}</span>}
      </header>
      <div className="focus-sheet-actions">
        <button type="button" className="focus-sheet-action" onClick={onRestore}>
          Restore
        </button>
        {row.kind === 'projects' && row.archivedTaskCount > 0 && (
          <button type="button" className="focus-sheet-action" onClick={onRestoreWithoutTasks}>
            Restore without its tasks
          </button>
        )}
        <button type="button" className="focus-sheet-action danger" onClick={onPurge}>
          Delete forever
        </button>
      </div>
      <button type="button" className="focus-sheet-cancel" onClick={onClose}>
        Cancel
      </button>
    </BottomSheet>
  )
}

interface FilterSheetProps {
  filters: TrashFilters
  rows: { projects: TrashRow[]; tasks: TrashRow[] }
  onClose: () => void
  onApply: (next: TrashFilters) => void
}

const TYPE_OPTIONS: [TrashTypeFilter, string][] = [
  ['all', 'All'],
  ['projects', 'Projects'],
  ['tasks', 'Tasks'],
]

/**
 * The M03f filter sheet reduced to what this route filters on: a search field
 * and a single-select type row. Pending until Apply; a scrim tap discards.
 */
export function TrashFilterSheet({ filters, rows, onClose, onApply }: FilterSheetProps) {
  const [pending, setPending] = useState(filters)
  const matching = matchingTrashRows(rows, pending)
  const count = matching.projects.length + matching.tasks.length

  return (
    <BottomSheet className="task-filter-sheet trash-filter-sheet" labelledBy="trash-filter-sheet-title" handleLabel="Close filters" onClose={onClose}>
      <header>
        <h2 id="trash-filter-sheet-title">Filters</h2>
        <button type="button" className="task-sheet-reset" onClick={() => setPending(EMPTY_TRASH_FILTERS)}>Reset</button>
      </header>
      <label className="task-sheet-search">
        <Search size={15} aria-hidden="true" />
        <input aria-label="Search trash" placeholder="Project or task" value={pending.search} onChange={(event) => setPending({ ...pending, search: event.target.value })} />
      </label>
      <fieldset>
        <legend>Type</legend>
        <div className="task-sheet-options">
          {TYPE_OPTIONS.map(([value, label]) => (
            <button type="button" key={value} aria-pressed={pending.type === value} onClick={() => setPending({ ...pending, type: value })}>{label}</button>
          ))}
        </div>
      </fieldset>
      <button type="button" className="task-sheet-apply" onClick={() => onApply(pending)}>
        Show {pluralize(count, 'item')}
      </button>
    </BottomSheet>
  )
}
