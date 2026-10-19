import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTrashCount } from './trashCountContext'
import { useTrash, type RestoreUndo, type SelectedTrashItem } from './useTrash'
import {
  EMPTY_TRASH_FILTERS,
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

/* M08f — the /trash page's behaviour, shared by the phone and desktop trees.
 *
 * Both trees read the same rows, filters, selection mode and undo bar; they
 * differ only in how a row is reached (ring + swipe + ⋯ sheet on the phone,
 * ring + hover/focus actions on desktop) and where the filter form opens.
 * Restore drops its confirms because the undo bar makes it reversible; purge
 * keeps every confirm it has and always names the full scope (#184, #189). */

export const UNDO_LIFETIME_MS = 5000

export type TrashMode = 'browse' | 'select'

export interface PendingUndo {
  label: string
  /** The server's account of what the restore changed — never the clicked row's
   * id, which an un-skip replaces with its successor's (#306). */
  undo: RestoreUndo
}

function toSelected(row: TrashRow): SelectedTrashItem {
  return {
    kind: row.kind,
    id: row.id,
    label: row.title,
    archivedTaskCount: row.archivedTaskCount,
    purgeTaskCount: row.purgeTaskCount,
  }
}

export function useTrashPage() {
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
  const [mode, setMode] = useState<TrashMode>('browse')
  const [selected, setSelected] = useState<Set<string>>(new Set())
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
  // with the server's exact removable total, never lower than what's on screen.
  const emptyTrashTotal = Math.max(counts.purge_total, loadedCount)

  const visibleRows = useMemo(() => [...shown.projects, ...shown.tasks], [shown])
  // Selection always intersects with what's shown: a hidden row is never acted on.
  const selectedRows = visibleRows.filter((row) => selected.has(rowKey(row)))
  const allSelected = visibleRows.length > 0 && visibleRows.every((row) => selected.has(rowKey(row)))

  function applyFilters(next: TrashFilters): void {
    setFilters(next)
    // Selection is cleared by any filter change: a hidden row is never acted on.
    setSelected(new Set())
  }

  function enterSelect(row?: TrashRow): void {
    setMode('select')
    setSelected(new Set(row ? [rowKey(row)] : []))
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

  function toggleAll(): void {
    setSelected(new Set(allSelected ? [] : visibleRows.map(rowKey)))
  }

  /** One restore. Cheap, so no confirm; recoverable, so an undo bar. A project
   * brings its archived tasks unless the caller says otherwise. */
  async function restore(row: TrashRow, bringTasks = row.archivedTaskCount > 0): Promise<void> {
    const receipt =
      row.kind === 'projects'
        ? await restoreProjectById(row.id, row.title, bringTasks)
        : await restoreTaskById(row.id, row.title)
    if (receipt) armUndo({ label: row.title, undo: receipt })
  }

  function undoLastRestore(): void {
    if (!undo) return
    const { undo: receipt, label } = undo
    dismissUndo()
    void undoRestore(receipt, label)
  }

  // Purge is irreversible, so every purge path is gated by a confirm naming the
  // full scope — for a project, every trashed task it owns (BUG #184, #189).
  function confirmPurge(row: TrashRow): void {
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

  return {
    loading,
    error,
    notice,
    counts,
    rows,
    shown,
    filters,
    chips,
    filtersActive,
    isEmpty,
    noMatches,
    totalCount,
    emptyTrashTotal,
    mode,
    selected,
    selectedRows,
    allSelected,
    undo,
    applyFilters,
    enterSelect,
    exitSelect,
    toggleSelected,
    toggleAll,
    restore,
    undoLastRestore,
    confirmPurge,
    confirmPurgeSelection,
    restoreSelection,
    confirmEmptyTrash,
  }
}

export type TrashPageState = ReturnType<typeof useTrashPage>
