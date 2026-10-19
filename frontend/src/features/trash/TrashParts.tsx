import type { ReactNode } from 'react'
import { SquareCheck, Trash2, X } from 'lucide-react'
import type { TrashKind } from './useTrash'
import type { PendingUndo, TrashPageState } from './useTrashPage'
import { EMPTY_TRASH_FILTERS, TRASH_GROUPS, deletedMeta, pluralize, rowNoun, type TrashRow } from './trashMeta'

/* M08f pieces both /trash trees render identically. Layout differences (the
 * phone's fixed bars over the bottom nav, desktop's sticky ones) live in CSS,
 * keyed off `.mobile-trash` / `.desktop-trash`. */

/** At most two facts, separated by a faint dot — words, never pills. */
export function TrashRowMeta({ row }: { row: TrashRow }) {
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

/** Selection mode: the checkbox takes the ring's column and the row is the label. */
export function TrashSelectRow({ row, checked, onToggle }: { row: TrashRow; checked: boolean; onToggle: () => void }) {
  return (
    <label className="trash-row selecting">
      <input
        type="checkbox"
        className="trash-row-check"
        aria-label={`Select ${rowNoun(row)} ${row.title}`}
        checked={checked}
        onChange={onToggle}
      />
      <span className="trash-row-main">
        <span className="trash-row-title">{row.title}</span>
        <TrashRowMeta row={row} />
      </span>
    </label>
  )
}

interface GroupProps {
  kind: TrashKind
  label: string
  page: TrashPageState
  renderRow: (row: TrashRow) => ReactNode
}

/** A mono group label with the true count beside it; the truncation note is a statement, not a control. */
export function TrashGroup({ kind, label, page, renderRow }: GroupProps) {
  const groupRows = page.shown[kind]
  if (groupRows.length === 0) return null
  const loaded = page.rows[kind].length
  const total = page.counts[kind]
  return (
    <section className="trash-group" aria-label={label}>
      <h2>
        <span>{label}</span>
        <span className="trash-group-count">{page.filtersActive ? groupRows.length : Math.max(total, loaded)}</span>
      </h2>
      <ul>{groupRows.map(renderRow)}</ul>
      {!page.filtersActive && loaded < total && (
        <p className="trash-truncated">Showing the {loaded} most recent of {total}</p>
      )}
    </section>
  )
}

/** Loading: the row's height, no spinner. */
export function TrashSkeleton() {
  return (
    <div className="trash-list" aria-busy="true" aria-label="Loading trash">
      {TRASH_GROUPS.map(({ kind, label }) => (
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
  )
}

export function TrashEmpty({ head, sub, onClear }: { head: string; sub?: string; onClear?: () => void }) {
  return (
    <div className="trash-empty">
      <Trash2 size={20} aria-hidden="true" />
      <span className="trash-empty-head">{head}</span>
      {sub && <span className="trash-empty-sub">{sub}</span>}
      {onClear && (
        <button type="button" className="trash-empty-clear" onClick={onClear}>
          Clear
        </button>
      )}
    </div>
  )
}

/** The chips row mirrors the applied filters one-to-one; the list is never filtered without it. */
export function TrashFilterChips({ page }: { page: TrashPageState }) {
  if (page.chips.length === 0) return null
  return (
    <div className="mobile-filter-chips trash-filter-chips" aria-label="Applied filters">
      {page.chips.map(({ label, next }) => (
        <button type="button" key={label} aria-label={`Remove ${label}`} onClick={() => page.applyFilters(next)}>
          {label}
          <X size={13} aria-hidden="true" />
        </button>
      ))}
      <button type="button" className="mobile-filters-clear" onClick={() => page.applyFilters(EMPTY_TRASH_FILTERS)}>
        Clear
      </button>
    </div>
  )
}

/** Selection mode swaps the title block for a live count and two text buttons. */
export function TrashSelectionHeading({ page }: { page: TrashPageState }) {
  return (
    <div className="trash-heading">
      <span className="trash-selection-count" role="status">
        {page.selectedRows.length} selected
      </span>
      <div className="trash-selection-actions">
        <button type="button" onClick={page.toggleAll}>
          {page.allSelected ? 'Select none' : 'Select all'}
        </button>
        <button type="button" onClick={page.exitSelect}>
          Done
        </button>
      </div>
    </div>
  )
}

/** What you have → what you can do about it → burn it. When Empty trash
 * removes more than the listed items (archived tasks go with their project),
 * the row names both numbers so it never contradicts the heading (#363). */
export function TrashFoot({ page }: { page: TrashPageState }) {
  return (
    <div className="trash-foot">
      <button type="button" onClick={() => page.enterSelect()}>
        <SquareCheck size={16} aria-hidden="true" />
        Select
      </button>
      <button type="button" className="danger" onClick={page.confirmEmptyTrash}>
        <Trash2 size={16} aria-hidden="true" />
        <span>
          Empty trash <span className="trash-meta-sep" aria-hidden="true">·</span>{' '}
          {page.emptyTrashExtra ? (
            <>
              {pluralize(page.totalCount, 'item')}
              <span className="trash-foot-extra">, {page.emptyTrashExtra}</span>
            </>
          ) : (
            page.emptyTrashTotal
          )}
        </span>
      </button>
    </div>
  )
}

export function TrashUndoBar({ undo, onUndo }: { undo: PendingUndo; onUndo: () => void }) {
  return (
    <div className="trash-undo" role="status">
      <span>
        Restored <span className="trash-meta-sep" aria-hidden="true">·</span> {undo.label}
      </span>
      <button type="button" onClick={onUndo}>
        Undo
      </button>
    </div>
  )
}

export function TrashSelectionBar({ page }: { page: TrashPageState }) {
  const count = page.selectedRows.length
  return (
    <div className="trash-action-bar" role="toolbar" aria-label="Selection actions">
      <span>{count ? pluralize(count, 'item') : 'Select items'}</span>
      <button type="button" disabled={count === 0} onClick={page.restoreSelection}>
        Restore
      </button>
      <button type="button" className="danger" disabled={count === 0} onClick={page.confirmPurgeSelection}>
        Delete forever
      </button>
    </div>
  )
}
