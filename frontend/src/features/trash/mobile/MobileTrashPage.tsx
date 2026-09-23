import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { MoreHorizontal, RotateCcw, SlidersHorizontal, SquareCheck, Trash2, X } from 'lucide-react'
import { useTrashCount } from '../trashCountContext'
import { useTrash, type SelectedTrashItem, type TrashKind } from '../useTrash'
import { TrashFilterSheet, TrashRowSheet } from './TrashSheets'
import { TrashSwipeRow } from './TrashSwipeRow'
import {
  EMPTY_TRASH_FILTERS,
  deletedMeta,
  matchingTrashRows,
  pluralize,
  purgeSelectionScope,
  rowKey,
  trashFilterChips,
  trashFiltersActive,
  trashRows,
  type TrashFilters,
  type TrashRow,
} from './trashMeta'

/* M08f — /trash at phone width.
 *
 * Find the thing you deleted by mistake and get it back; everything else on the
 * route is secondary to that sentence. Six controls for two verbs collapse into
 * a ring (restore), a swipe (restore), a ⋯ sheet (restore, restore without
 * tasks, delete forever) and two foot rows (Select, Empty trash). Restore drops
 * its confirms because the undo bar makes it reversible; purge keeps every
 * confirm it has, and is not reachable by gesture at all.
 *
 * One divergence from the handoff: the retention countdown. It needs a purge
 * policy the backend does not have, so the sub-line carries one fact and the
 * meta line never says `{n} days left` — the handoff's own fallback. */

export const UNDO_LIFETIME_MS = 5000

type Mode = 'browse' | 'select'

interface PendingUndo {
  label: string
  kind: TrashKind
  id: number
}

const GROUPS: { kind: TrashKind; label: string }[] = [
  { kind: 'projects', label: 'Projects' },
  { kind: 'tasks', label: 'Tasks' },
]

function toSelected(row: TrashRow): SelectedTrashItem {
  return {
    kind: row.kind,
    id: row.id,
    label: row.title,
    archivedTaskCount: row.archivedTaskCount,
    purgeTaskCount: row.purgeTaskCount,
  }
}

export function MobileTrashPage() {
  const {
    trash,
    loading,
    error,
    notice,
    restoreProjectById,
    restoreTaskById,
    restoreItems,
    purgeById,
    purgeItems,
    emptyTrashAll,
    undoRestore,
  } = useTrash()
  // True per-kind totals, unbounded by the /trash list page (see /trash/count).
  const { counts } = useTrashCount()

  const [filters, setFilters] = useState<TrashFilters>(EMPTY_TRASH_FILTERS)
  const [mode, setMode] = useState<Mode>('browse')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [rowTarget, setRowTarget] = useState<TrashRow | null>(null)
  const [undo, setUndo] = useState<PendingUndo | null>(null)
  const undoTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => () => { if (undoTimer.current) clearTimeout(undoTimer.current) }, [])

  const armUndo = useCallback((pending: PendingUndo) => {
    if (undoTimer.current) clearTimeout(undoTimer.current)
    setUndo(pending)
    undoTimer.current = setTimeout(() => setUndo(null), UNDO_LIFETIME_MS)
  }, [])

  const dismissUndo = useCallback(() => {
    if (undoTimer.current) clearTimeout(undoTimer.current)
    setUndo(null)
  }, [])

  const rows = useMemo(() => trashRows(trash), [trash])
  const shown = useMemo(() => matchingTrashRows(rows, filters), [rows, filters])
  const chips = trashFilterChips(filters)
  const filtersActive = trashFiltersActive(filters)
  const loadedCount = rows.projects.length + rows.tasks.length
  const isEmpty = !loading && loadedCount === 0
  const noMatches = !loading && !isEmpty && shown.projects.length === 0 && shown.tasks.length === 0
  // The sub-line's item count is the true total, never lower than what's loaded.
  const totalCount = Math.max(counts.projects + counts.tasks, loadedCount)
  // Empty trash purges everything server-side, cascade tasks included; confirm
  // with the server's exact removable total (see the desktop page).
  const emptyTrashTotal = Math.max(counts.purge_total, loadedCount)

  const visibleRows = useMemo(() => [...shown.projects, ...shown.tasks], [shown])
  const selectedRows = visibleRows.filter((row) => selected.has(rowKey(row)))

  function applyFilters(next: TrashFilters): void {
    setFilters(next)
    // Selection is cleared by any filter change: a hidden row is never acted on.
    setSelected(new Set())
    setFiltersOpen(false)
  }

  function enterSelect(row?: TrashRow): void {
    setMode('select')
    setSelected(new Set(row ? [rowKey(row)] : []))
    setRowTarget(null)
  }

  function exitSelect(): void {
    setMode('browse')
    setSelected(new Set())
  }

  function toggleSelected(row: TrashRow): void {
    setSelected((prev) => {
      const next = new Set(prev)
      const key = rowKey(row)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const allSelected = visibleRows.length > 0 && visibleRows.every((row) => selected.has(rowKey(row)))

  /** One restore, from the ring, the swipe or the sheet. Cheap, so no confirm; recoverable, so an undo bar. */
  async function restore(row: TrashRow, bringTasks = row.archivedTaskCount > 0): Promise<void> {
    setRowTarget(null)
    const ok =
      row.kind === 'projects'
        ? await restoreProjectById(row.id, row.title, row.archivedTaskCount, bringTasks)
        : await restoreTaskById(row.id, row.title)
    if (ok) armUndo({ label: row.title, kind: row.kind, id: row.id })
  }

  // Purge is irreversible, so every purge path is gated by a confirm naming the
  // full scope — for a project, every trashed task it owns (BUG #184, #189).
  function confirmPurge(row: TrashRow): void {
    setRowTarget(null)
    const scope =
      row.purgeTaskCount > 0
        ? `“${row.title}” and the ${pluralize(row.purgeTaskCount, 'trashed task')} it owns`
        : `“${row.title}”`
    if (window.confirm(`Permanently delete ${scope}? This cannot be undone.`)) {
      void purgeById(row.kind, row.id, row.title, row.purgeTaskCount)
    }
  }

  function confirmPurgeSelection(): void {
    if (selectedRows.length === 0) return
    if (window.confirm(`Permanently delete ${purgeSelectionScope(selectedRows)}? This cannot be undone.`)) {
      const items = selectedRows.map(toSelected)
      exitSelect()
      void purgeItems(items)
    }
  }

  function restoreSelection(): void {
    if (selectedRows.length === 0) return
    const items = selectedRows.map(toSelected)
    exitSelect()
    void restoreItems(items)
  }

  function confirmEmptyTrash(): void {
    if (
      window.confirm(
        `Permanently delete all ${pluralize(emptyTrashTotal, 'item')} in trash, including any tasks archived with deleted projects? This cannot be undone.`,
      )
    ) {
      void emptyTrashAll()
    }
  }

  function renderMeta(row: TrashRow) {
    const facts = deletedMeta(row)
    if (facts.length === 0) return null
    return (
      <span className="trash-row-meta">
        {facts.map((fact, index) => (
          <span key={fact}>
            {index > 0 && <span className="trash-meta-sep" aria-hidden="true">· </span>}
            {fact}
          </span>
        ))}
      </span>
    )
  }

  function renderRow(row: TrashRow) {
    const noun = row.kind === 'projects' ? 'project' : 'task'
    if (mode === 'select') {
      const key = rowKey(row)
      return (
        <li key={key}>
          <label className="trash-row selecting">
            <input
              type="checkbox"
              className="trash-row-check"
              aria-label={`Select ${noun} ${row.title}`}
              checked={selected.has(key)}
              onChange={() => toggleSelected(row)}
            />
            <span className="trash-row-main">
              <span className="trash-row-title">{row.title}</span>
              {renderMeta(row)}
            </span>
          </label>
        </li>
      )
    }
    return (
      <li key={rowKey(row)}>
        <TrashSwipeRow enabled onRestore={() => void restore(row)} onLongPress={() => enterSelect(row)}>
          <div className="trash-row">
            <button
              type="button"
              className="trash-restore-ring"
              aria-label={`Restore ${noun} ${row.title}`}
              onClick={() => void restore(row)}
            >
              <RotateCcw size={12} aria-hidden="true" />
            </button>
            <span className="trash-row-main">
              <span className="trash-row-title">{row.title}</span>
              {renderMeta(row)}
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

  function renderGroup({ kind, label }: { kind: TrashKind; label: string }) {
    const groupRows = shown[kind]
    if (groupRows.length === 0) return null
    const loaded = rows[kind].length
    const total = counts[kind]
    return (
      <section key={kind} className="trash-group" aria-label={label}>
        <h2>
          <span>{label}</span>
          <span className="trash-group-count">{filtersActive ? groupRows.length : Math.max(total, loaded)}</span>
        </h2>
        <ul>{groupRows.map(renderRow)}</ul>
        {!filtersActive && loaded < total && (
          <p className="trash-truncated">Showing the {loaded} most recent of {total}</p>
        )}
      </section>
    )
  }

  const emptyState = (head: string, sub: string | null, clear: boolean) => (
    <div className="trash-empty">
      <Trash2 size={20} aria-hidden="true" />
      <span className="trash-empty-head">{head}</span>
      {sub && <span className="trash-empty-sub">{sub}</span>}
      {clear && (
        <button type="button" className="trash-empty-clear" onClick={() => applyFilters(EMPTY_TRASH_FILTERS)}>
          Clear
        </button>
      )}
    </div>
  )

  return (
    <div className="mobile-trash">
      {mode === 'select' ? (
        <div className="trash-heading">
          <span className="trash-selection-count" role="status">
            {selectedRows.length} selected
          </span>
          <div className="trash-selection-actions">
            <button
              type="button"
              onClick={() => setSelected(new Set(allSelected ? [] : visibleRows.map(rowKey)))}
            >
              {allSelected ? 'Select none' : 'Select all'}
            </button>
            <button type="button" onClick={exitSelect}>
              Done
            </button>
          </div>
        </div>
      ) : (
        <div className="trash-heading">
          <div className="trash-heading-name">
            <h1>Trash</h1>
            {!loading && !isEmpty && (
              <p className="trash-heading-sub">{pluralize(totalCount, 'item')}</p>
            )}
          </div>
          {!loading && !isEmpty && (
            <button
              type="button"
              className={`mobile-filter-control${chips.length ? ' applied' : ''}`}
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

      {error && <p role="alert" className="error">{error}</p>}
      {notice && !undo && <p role="status" className="trash-notice">{notice}</p>}

      {chips.length > 0 && (
        <div className="mobile-filter-chips" aria-label="Applied filters">
          {chips.map(({ label, next }) => (
            <button type="button" key={label} aria-label={`Remove ${label}`} onClick={() => applyFilters(next)}>
              {label}
              <X size={13} aria-hidden="true" />
            </button>
          ))}
          <button type="button" className="mobile-filters-clear" onClick={() => applyFilters(EMPTY_TRASH_FILTERS)}>
            Clear
          </button>
        </div>
      )}

      {loading && (
        <div className="trash-list" aria-busy="true" aria-label="Loading trash">
          {GROUPS.map(({ kind, label }) => (
            <section key={kind} className="trash-group" aria-hidden="true">
              <h2><span>{label}</span></h2>
              <ul>
                {(kind === 'projects' ? [0] : [0, 1]).map((index) => (
                  <li key={index}>
                    <div className="trash-skeleton">
                      <span />
                      <span className="trash-skeleton-lines"><span /><span /></span>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      {isEmpty && emptyState('Nothing in the trash', 'Deleted projects and tasks land here.', false)}
      {noMatches && emptyState('No items match your filters', null, true)}

      {!loading && !isEmpty && !noMatches && (
        <div className="trash-list">
          {GROUPS.map(renderGroup)}
          {mode === 'browse' && (
            <div className="trash-foot">
              <button type="button" onClick={() => enterSelect()}>
                <SquareCheck size={16} aria-hidden="true" />
                Select
              </button>
              <button type="button" className="danger" onClick={confirmEmptyTrash}>
                <Trash2 size={16} aria-hidden="true" />
                Empty trash <span className="trash-meta-sep" aria-hidden="true">·</span> {emptyTrashTotal}
              </button>
            </div>
          )}
        </div>
      )}

      {undo && (
        <div className="trash-undo" role="status">
          <span>
            Restored <span className="trash-meta-sep" aria-hidden="true">·</span> {undo.label}
          </span>
          <button
            type="button"
            onClick={() => {
              const { kind, id, label } = undo
              dismissUndo()
              void undoRestore(kind, id, label)
            }}
          >
            Undo
          </button>
        </div>
      )}

      {mode === 'select' && !undo && (
        <div className="trash-action-bar" role="toolbar" aria-label="Selection actions">
          <span>{selectedRows.length ? pluralize(selectedRows.length, 'item') : 'Select items'}</span>
          <button type="button" disabled={selectedRows.length === 0} onClick={restoreSelection}>
            Restore
          </button>
          <button type="button" className="danger" disabled={selectedRows.length === 0} onClick={confirmPurgeSelection}>
            Delete forever
          </button>
        </div>
      )}

      {rowTarget && (
        <TrashRowSheet
          row={rowTarget}
          onRestore={() => void restore(rowTarget)}
          onRestoreWithoutTasks={() => void restore(rowTarget, false)}
          onPurge={() => confirmPurge(rowTarget)}
          onClose={() => setRowTarget(null)}
        />
      )}

      {filtersOpen && (
        <TrashFilterSheet filters={filters} rows={rows} onClose={() => setFiltersOpen(false)} onApply={applyFilters} />
      )}
    </div>
  )
}
