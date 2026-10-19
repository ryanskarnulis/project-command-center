import { useEffect, useRef, useState, type MouseEvent, type RefObject } from 'react'
import { RotateCcw, SlidersHorizontal } from 'lucide-react'
import { useMobileViewport } from '../../hooks/useMobileViewport'
import { MobileTrashPage } from './mobile/MobileTrashPage'
import { TrashFilterForm } from './TrashFilterForm'
import {
  TrashEmpty,
  TrashFilterChips,
  TrashFoot,
  TrashGroup,
  TrashRowMeta,
  TrashSelectRow,
  TrashSelectionBar,
  TrashSelectionHeading,
  TrashSkeleton,
  TrashUndoBar,
} from './TrashParts'
import { EMPTY_TRASH_FILTERS, TRASH_GROUPS, rowKey, rowNoun, type TrashFilters, type TrashRow } from './trashMeta'
import { useTrashPage, type TrashPageState } from './useTrashPage'

export function TrashPage() {
  // The same hook Tasks, Project detail and Focus branch on. The breakpoint
  // stays inside it; below it the route renders the M08f phone tree.
  const mobile = useMobileViewport()
  if (mobile) {
    return (
      <main className="trash-page-mobile">
        <MobileTrashPage />
      </main>
    )
  }
  return <DesktopTrashPage />
}

/** Shift-mousedown would extend the browser's text selection across rows. */
function suppressShiftTextSelection(event: MouseEvent): void {
  if (event.shiftKey) event.preventDefault()
}

/* M08f at desktop width (#323).
 *
 * The phone's rows, group labels, chips row, foot rows, undo bar and selection
 * mode, driven by the same `useTrashPage`. What changes is the input: the
 * swipe and the ⋯ sheet become row actions that appear on hover or keyboard
 * focus (Restore without its tasks, Delete forever); the 500ms hold that
 * enters selection mode becomes a Ctrl/⌘-click on the row; Escape leaves
 * selection mode; and the filter sheet becomes a panel anchored to the filter
 * control. The ring still restores in one click with no confirm, because the
 * undo bar makes it reversible. */
function DesktopTrashPage() {
  const page = useTrashPage()
  const { loading, isEmpty, noMatches, mode, undo } = page
  const [filtersOpen, setFiltersOpen] = useState(false)
  const filterControl = useRef<HTMLButtonElement>(null)

  // Escape leaves selection mode — the keyboard's Done. The filter panel owns
  // Escape while it is open.
  const exitSelect = page.exitSelect
  useEffect(() => {
    if (mode !== 'select' || filtersOpen) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') exitSelect()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [mode, filtersOpen, exitSelect])

  function applyFilters(next: TrashFilters): void {
    page.applyFilters(next)
    setFiltersOpen(false)
  }

  function renderRow(row: TrashRow) {
    const key = rowKey(row)
    if (mode === 'select') {
      // Shift-click checks the range from the anchor to here (#365). Captured
      // so the label never also toggles this row's checkbox.
      const rangeOnShift = (event: MouseEvent) => {
        if (!event.shiftKey) return
        event.preventDefault()
        event.stopPropagation()
        page.selectRange(row)
      }
      return (
        <li key={key} onMouseDown={suppressShiftTextSelection} onClickCapture={rangeOnShift}>
          <TrashSelectRow row={row} checked={page.selected.has(key)} onToggle={() => page.toggleSelected(row)} />
        </li>
      )
    }
    const noun = rowNoun(row)
    // Ctrl/⌘-click anywhere on the row is the pointer's long-press: it enters
    // selection mode with this row checked, and swallows the click so it never
    // also restores or purges. Shift-click does the same with a range, which
    // with no anchor yet is just this row.
    const selectOnModifier = (event: MouseEvent) => {
      if (!(event.metaKey || event.ctrlKey || event.shiftKey)) return
      event.preventDefault()
      event.stopPropagation()
      if (event.shiftKey) page.selectRange(row)
      else page.enterSelect(row)
    }
    return (
      <li key={key}>
        <div className="trash-row" onMouseDown={suppressShiftTextSelection} onClickCapture={selectOnModifier}>
          <button
            type="button"
            className="trash-restore-ring"
            aria-label={`Restore ${noun} ${row.title}`}
            title="Restore"
            onClick={() => void page.restore(row)}
          >
            <RotateCcw size={12} aria-hidden="true" />
          </button>
          <span className="trash-row-main">
            <span className="trash-row-title">{row.title}</span>
            <TrashRowMeta row={row} />
          </span>
          <span className="trash-row-actions">
            {row.kind === 'projects' && row.archivedTaskCount > 0 && (
              <button
                type="button"
                aria-label={`Restore project ${row.title} without its tasks`}
                onClick={() => void page.restore(row, false)}
              >
                Restore without its tasks
              </button>
            )}
            <button
              type="button"
              className="danger"
              aria-label={`Delete ${noun} ${row.title} forever`}
              onClick={() => page.confirmPurge(row)}
            >
              Delete forever
            </button>
          </span>
        </div>
      </li>
    )
  }

  return (
    <main className="desktop-trash">
      {mode === 'select' ? (
        <TrashSelectionHeading page={page} />
      ) : (
        <div className="trash-heading">
          <div className="trash-heading-name">
            <h1>Trash</h1>
            {!loading && !isEmpty && (
              <p className="trash-heading-sub">{page.countLine}</p>
            )}
          </div>
          {!loading && !isEmpty && (
            <div className="trash-filter-anchor">
              <button
                ref={filterControl}
                type="button"
                className={`trash-filter-control${page.chips.length ? ' applied' : ''}`}
                aria-label="Filter trash"
                aria-haspopup="dialog"
                aria-expanded={filtersOpen}
                onClick={() => setFiltersOpen((open) => !open)}
              >
                <SlidersHorizontal size={15} aria-hidden="true" />
                Filter
              </button>
              {filtersOpen && (
                <TrashFilterPanel
                  page={page}
                  control={filterControl}
                  onApply={applyFilters}
                  onClose={() => setFiltersOpen(false)}
                />
              )}
            </div>
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
    </main>
  )
}

interface FilterPanelProps {
  page: TrashPageState
  control: RefObject<HTMLButtonElement | null>
  onApply: (next: TrashFilters) => void
  onClose: () => void
}

/**
 * The phone's filter sheet as a panel under its control: the same pending form
 * (Enter or **Show n items** applies), dismissed without applying by Escape or
 * a click outside — Escape hands focus back to the control.
 */
function TrashFilterPanel({ page, control, onApply, onClose }: FilterPanelProps) {
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (panel.current?.contains(target) || control.current?.contains(target)) return
      onClose()
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      onClose()
      control.current?.focus()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [control, onClose])

  return (
    <div ref={panel} className="trash-filter-panel" role="dialog" aria-labelledby="trash-filter-panel-title">
      <TrashFilterForm
        filters={page.filters}
        rows={page.rows}
        titleId="trash-filter-panel-title"
        onApply={onApply}
        autoFocusSearch
      />
    </div>
  )
}
