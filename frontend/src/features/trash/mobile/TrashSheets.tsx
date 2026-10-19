import { BottomSheet } from '../../../components/BottomSheet'
import { formatRelative } from '../../../utils/dates'
import { TrashFilterForm } from '../TrashFilterForm'
import type { TrashFilters, TrashRow } from '../trashMeta'

/* The two M08f bottom sheets, on the shared `BottomSheet` primitive. */

interface RowSheetProps {
  row: TrashRow
  onRestore: () => void
  /** Projects only: the `restoreSubtasks` choice, asked as its own row. */
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

/** The shared filter form in a bottom sheet. Pending until Apply; a scrim tap discards. */
export function TrashFilterSheet({ filters, rows, onClose, onApply }: FilterSheetProps) {
  return (
    <BottomSheet className="task-filter-sheet trash-filter-sheet" labelledBy="trash-filter-sheet-title" handleLabel="Close filters" onClose={onClose}>
      <TrashFilterForm filters={filters} rows={rows} titleId="trash-filter-sheet-title" onApply={onApply} />
    </BottomSheet>
  )
}
