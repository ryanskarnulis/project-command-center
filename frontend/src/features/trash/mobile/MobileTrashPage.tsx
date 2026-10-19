import { useState } from 'react'
import { MoreHorizontal, RotateCcw, SlidersHorizontal } from 'lucide-react'
import { useTrashPage } from '../useTrashPage'
import {
  TrashEmpty,
  TrashFilterChips,
  TrashFoot,
  TrashGroup,
  TrashHeadingSub,
  TrashRowMeta,
  TrashSelectRow,
  TrashSelectionBar,
  TrashSelectionHeading,
  TrashSkeleton,
  TrashUndoBar,
} from '../TrashParts'
import { TrashFilterSheet, TrashRowSheet } from './TrashSheets'
import { TrashSwipeRow } from './TrashSwipeRow'
import { EMPTY_TRASH_FILTERS, TRASH_GROUPS, rowKey, rowNoun, type TrashFilters, type TrashRow } from '../trashMeta'

/* M08f — /trash at phone width.
 *
 * Find the thing you deleted by mistake and get it back; everything else on the
 * route is secondary to that sentence. Six controls for two verbs collapse into
 * a ring (restore), a swipe (restore), a ⋯ sheet (restore, restore without
 * tasks, delete forever) and two foot rows (Select, Empty trash). Restore drops
 * its confirms because the undo bar makes it reversible; purge keeps every
 * confirm it has, and is not reachable by gesture at all. The behaviour is
 * `useTrashPage`, shared with the desktop tree.
 *
 * The retention countdown (#321) follows the server's window: the sub-line adds
 * `removed after {n} days`, and a row in its last week adds `{n} days left` in
 * the warning color. With retention off (0) both drop — the handoff's own
 * fallback. */

export function MobileTrashPage() {
  const page = useTrashPage()
  const { loading, isEmpty, noMatches, mode, undo } = page
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [rowTarget, setRowTarget] = useState<TrashRow | null>(null)

  function applyFilters(next: TrashFilters): void {
    page.applyFilters(next)
    setFiltersOpen(false)
  }

  function restore(row: TrashRow, bringTasks?: boolean): void {
    setRowTarget(null)
    void page.restore(row, bringTasks)
  }

  function renderRow(row: TrashRow) {
    const key = rowKey(row)
    if (mode === 'select') {
      return (
        <li key={key}>
          <TrashSelectRow row={row} checked={page.selected.has(key)} onToggle={() => page.toggleSelected(row)} />
        </li>
      )
    }
    const noun = rowNoun(row)
    return (
      <li key={key}>
        <TrashSwipeRow enabled onRestore={() => restore(row)} onLongPress={() => { setRowTarget(null); page.enterSelect(row) }}>
          <div className="trash-row">
            <button
              type="button"
              className="trash-restore-ring"
              aria-label={`Restore ${noun} ${row.title}`}
              onClick={() => restore(row)}
            >
              <RotateCcw size={12} aria-hidden="true" />
            </button>
            <span className="trash-row-main">
              <span className="trash-row-title">{row.title}</span>
              <TrashRowMeta row={row} />
            </span>
            <button
              type="button"
              className="trash-more"
              aria-label={`Actions for ${noun} ${row.title}`}
              aria-haspopup="dialog"
              onClick={() => setRowTarget(row)}
            >
              <MoreHorizontal size={18} aria-hidden="true" />
            </button>
          </div>
        </TrashSwipeRow>
      </li>
    )
  }

  return (
    <div className="mobile-trash">
      {mode === 'select' ? (
        <TrashSelectionHeading page={page} />
      ) : (
        <div className="trash-heading">
          <div className="trash-heading-name">
            <h1>Trash</h1>
            {!loading && !isEmpty && (
              <TrashHeadingSub page={page} />
            )}
          </div>
          {!loading && !isEmpty && (
            <button
              type="button"
              className={`mobile-filter-control${page.chips.length ? ' applied' : ''}`}
              aria-label="Filter trash"
              aria-haspopup="dialog"
              aria-expanded={filtersOpen}
              onClick={() => setFiltersOpen(true)}
            >
              <SlidersHorizontal size={18} aria-hidden="true" />
            </button>
          )}
        </div>
      )}

      {page.error && <p role="alert" className="error">{page.error}</p>}
      {page.notice && !undo && <p role="status" className="trash-notice">{page.notice}</p>}

      <TrashFilterChips page={page} />

      {loading && <TrashSkeleton />}

      {isEmpty && <TrashEmpty head="Nothing in the trash" sub="Deleted projects and tasks land here." />}
      {noMatches && <TrashEmpty head="No items match your filters" onClear={() => applyFilters(EMPTY_TRASH_FILTERS)} />}

      {!loading && !isEmpty && !noMatches && (
        <div className="trash-list">
          {TRASH_GROUPS.map(({ kind, label }) => (
            <TrashGroup key={kind} kind={kind} label={label} page={page} renderRow={renderRow} />
          ))}
          {mode === 'browse' && <TrashFoot page={page} />}
        </div>
      )}

      {undo && <TrashUndoBar undo={undo} onUndo={page.undoLastRestore} />}

      {mode === 'select' && !undo && <TrashSelectionBar page={page} />}

      {rowTarget && (
        <TrashRowSheet
          row={rowTarget}
          onRestore={() => restore(rowTarget)}
          onRestoreWithoutTasks={() => restore(rowTarget, false)}
          onPurge={() => { setRowTarget(null); page.confirmPurge(rowTarget) }}
          onClose={() => setRowTarget(null)}
        />
      )}

      {filtersOpen && (
        <TrashFilterSheet filters={page.filters} rows={page.rows} onClose={() => setFiltersOpen(false)} onApply={applyFilters} />
      )}
    </div>
  )
}
