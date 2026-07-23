import { useMemo, useRef, useEffect, useState, type ReactNode } from 'react'
import { FolderX, Search, SlidersHorizontal, Trash2 } from 'lucide-react'
import { useTrash, type RestoreItem, type TrashKind } from './useTrash'
import { useTrashCount } from './trashCountContext'
import { TaskCard } from '../tasks/TaskCard'
import { ProjectCard } from '../projects/ProjectCard'
import { formatRelative } from '../../utils/dates'

// A tri-state "select all" for one section: checked when every visible item is
// selected, indeterminate when only some are. Toggling selects/clears all
// currently visible items in the section.
function SelectAll({
  ids,
  selected,
  onChange,
}: {
  ids: number[]
  selected: Set<number>
  onChange: (select: boolean) => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  const allSelected = ids.length > 0 && ids.every((id) => selected.has(id))
  const someSelected = ids.some((id) => selected.has(id))
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = someSelected && !allSelected
  }, [someSelected, allSelected])
  return (
    <label className="trash-select-all">
      <input
        ref={ref}
        type="checkbox"
        checked={allSelected}
        onChange={() => onChange(!allSelected)}
      />
      Select all
    </label>
  )
}

// The multi-restore / multi-purge action bar, shown for a section once at least
// one of its items is selected.
function BulkBar({
  count,
  onRestore,
  onDelete,
}: {
  count: number
  onRestore: () => void
  onDelete: () => void
}) {
  if (count === 0) return null
  return (
    <div className="bulk-actions trash-bulk-actions">
      <span className="trash-selected-count">{count} selected</span>
      <button type="button" className="secondary-action" onClick={onRestore}>
        Restore selected
      </button>
      <button type="button" className="trash-danger" onClick={onDelete}>
        Delete selected
      </button>
    </div>
  )
}

function DeletedAt({ at }: { at?: string | null }) {
  if (!at) return null
  return <span className="trash-deleted-at">Deleted {formatRelative(at)}</span>
}

// The /trash list is page-limited, so the loaded set can be a prefix of what's
// really in trash. When the true (unbounded) count exceeds what we loaded, warn
// that the section is showing only the most recent slice.
function TruncationHint({ loaded, total }: { loaded: number; total: number }) {
  if (loaded >= total) return null
  return (
    <p className="trash-section-hint">
      Showing the {loaded} most recently deleted of {total}.
    </p>
  )
}

/**
 * The shared cards are <Link>s to detail pages. In trash we want their context,
 * not navigation to a soft-deleted item's (404'ing) detail page. Pre-empting the
 * default in the capture phase makes React Router's Link skip navigation while
 * the action buttons inside still fire.
 */
function NoNav({ children }: { children: ReactNode }) {
  return <div onClickCapture={(e) => e.preventDefault()}>{children}</div>
}

type TypeFilter = 'all' | 'projects' | 'tasks'

export function TrashPage() {
  const {
    trash,
    loading,
    error,
    notice,
    restoreProjectById,
    restoreTaskById,
    restoreAll,
    purgeById,
    purgeAll,
    emptyTrashAll,
  } = useTrash()
  // True per-kind totals, unbounded by the /trash list page (see /trash/count).
  const { counts } = useTrashCount()

  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')

  // Per-section checkbox selection, keyed by TrashKind. Ids that scroll out of
  // the filtered view simply lose their checkbox; bulk actions always intersect
  // with the items actually shown, so a hidden id is never acted on.
  const [selected, setSelected] = useState<Record<TrashKind, Set<number>>>(() => ({
    projects: new Set(),
    tasks: new Set(),
  }))

  const toggleItem = (kind: TrashKind, id: number) =>
    setSelected((prev) => {
      const next = new Set(prev[kind])
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return { ...prev, [kind]: next }
    })

  const toggleAll = (kind: TrashKind, ids: number[], select: boolean) =>
    setSelected((prev) => ({ ...prev, [kind]: new Set(select ? ids : []) }))

  const clearSelection = (kind: TrashKind) =>
    setSelected((prev) => ({ ...prev, [kind]: new Set() }))

  const totalCount = trash.projects.length + trash.tasks.length
  const isEmpty = totalCount === 0

  // Purge is irreversible (it really deletes the row), so every purge path is
  // gated by an explicit confirm naming what's about to go.
  const confirmPurge = (kind: TrashKind, id: number, label: string) => {
    if (window.confirm(`Permanently delete “${label}”? This cannot be undone.`)) {
      void purgeById(kind, id, label)
    }
  }

  // Empty trash purges *everything* server-side — the whole trash, not just the
  // loaded page, and including tasks archived with deleted projects. Confirm with
  // the server's exact removable total (counts.purge_total) so the number never
  // understates what's about to be destroyed. Falls back to the loaded length
  // only if the count hasn't arrived, and never reads lower than what's on screen.
  const emptyTrashTotal = Math.max(counts.purge_total, totalCount)
  const confirmEmptyTrash = () => {
    if (
      window.confirm(
        `Permanently delete all ${emptyTrashTotal} item${emptyTrashTotal === 1 ? '' : 's'} in trash, including any tasks archived with deleted projects? This cannot be undone.`,
      )
    ) {
      void emptyTrashAll()
    }
  }

  // Bulk-restore the selected items in a section, then drop the selection (the
  // reload that follows removes those rows anyway).
  const restoreSelected = (kind: TrashKind, items: RestoreItem[]) => {
    clearSelection(kind)
    void restoreAll(kind, items)
  }

  // Bulk-purge is irreversible, so confirm with the count first.
  const purgeSelected = (kind: TrashKind, ids: number[]) => {
    if (ids.length === 0) return
    if (
      window.confirm(
        `Permanently delete ${ids.length} item${ids.length === 1 ? '' : 's'}? This cannot be undone.`,
      )
    ) {
      clearSelection(kind)
      void purgeAll(kind, ids)
    }
  }

  // Search (case-insensitive, over each item's display label) + type filter are
  // both client-side. A list is included only when the type filter selects it;
  // the search then narrows within whatever's included.
  const { projects, tasks } = useMemo(() => {
    const q = search.trim().toLowerCase()
    const showProjects = typeFilter === 'all' || typeFilter === 'projects'
    const showTasks = typeFilter === 'all' || typeFilter === 'tasks'
    const match = (label: string) => q === '' || label.toLowerCase().includes(q)
    return {
      projects: showProjects ? trash.projects.filter((p) => match(p.name)) : [],
      tasks: showTasks ? trash.tasks.filter((t) => match(t.title)) : [],
    }
  }, [trash, search, typeFilter])

  const filtersActive = search.trim() !== '' || typeFilter !== 'all'
  const noMatches = !isEmpty && projects.length === 0 && tasks.length === 0

  // The number shown next to a section title. While filtering, the filtered
  // length is what's honest (it matches the cards shown). Otherwise show the true
  // total, falling back to the loaded length if the count hasn't arrived (or no
  // TrashCountProvider is mounted), so the heading never reads lower than what's
  // on screen.
  const headingCount = (filtered: number, loaded: number, total: number) =>
    filtersActive ? filtered : Math.max(total, loaded)

  // "Restore all" only ever restores the rows currently shown — the filtered
  // subset of the loaded page, which can be a prefix of the whole section. Label
  // it with that true scope so it never implies it cleared more than it did:
  // "N shown" while filtering, "N loaded" when the page is a truncated prefix,
  // and a plain "Restore all" only when the loaded set really is everything.
  const restoreAllLabel = (shown: number, loaded: number, total: number) =>
    filtersActive
      ? `Restore ${shown} shown`
      : loaded < total
        ? `Restore ${loaded} loaded`
        : 'Restore all'

  // Restore payloads for the visible items in each section (label + project
  // cascade count), reused by both "Restore all" and the selection bar.
  const projectItems: RestoreItem[] = projects.map((p) => ({
    id: p.id,
    label: p.name,
    archivedTaskCount: p.archived_task_count,
  }))
  const taskItems: RestoreItem[] = tasks.map((t) => ({ id: t.id, label: t.title }))
  // The subset of each section currently checked (intersected with what's shown).
  const selectedIn = (kind: TrashKind, items: RestoreItem[]) =>
    items.filter((i) => selected[kind].has(i.id))

  return (
    <main>
      <div className="section-heading">
        <h1>Trash</h1>
        {!loading && !isEmpty && (
          <button type="button" className="trash-danger" onClick={confirmEmptyTrash}>
            Empty trash
          </button>
        )}
      </div>
      <p>Recently deleted items. Restore anything you removed by mistake.</p>

      {error && <p role="alert" className="error">{error}</p>}
      {notice && <p role="status" className="trash-notice">{notice}</p>}
      {loading && <div className="page-loading">Loading trash…</div>}

      {!loading && isEmpty && (
        <div className="empty-state">
          <Trash2 size={20} aria-hidden="true" />
          Trash is empty.
        </div>
      )}

      {!loading && !isEmpty && (
        <div className="task-filters" role="search" aria-label="Filter trash">
          <div className="task-filters-header">
            <div className="task-filters-title">
              <SlidersHorizontal size={17} aria-hidden="true" />
              <strong>Filters</strong>
            </div>
            {filtersActive && (
              <button
                type="button"
                className="secondary-action"
                onClick={() => {
                  setSearch('')
                  setTypeFilter('all')
                }}
              >
                Clear
              </button>
            )}
          </div>

          <label className="task-search-field">
            <span>Search</span>
            <div>
              <Search size={17} aria-hidden="true" />
              <input
                aria-label="Search trash"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Project or task"
              />
            </div>
          </label>

          <div className="task-filter-grid">
            <label>
              <span>Type</span>
              <select
                aria-label="Filter by type"
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value as TypeFilter)}
              >
                <option value="all">All</option>
                <option value="projects">Projects</option>
                <option value="tasks">Tasks</option>
              </select>
            </label>
          </div>
        </div>
      )}

      {noMatches && (
        <div className="empty-state">No items match your search.</div>
      )}

      {!loading && projects.length > 0 && (
        <section className="trash-section">
          <div className="trash-section-head">
            <h2>
              <FolderX size={18} aria-hidden="true" />
              Projects ({headingCount(projects.length, trash.projects.length, counts.projects)})
            </h2>
            <SelectAll
              ids={projectItems.map((i) => i.id)}
              selected={selected.projects}
              onChange={(select) =>
                toggleAll('projects', projectItems.map((i) => i.id), select)
              }
            />
            <button
              type="button"
              className="secondary-action"
              onClick={() => void restoreAll('projects', projectItems)}
            >
              {restoreAllLabel(projects.length, trash.projects.length, counts.projects)}
            </button>
          </div>
          <BulkBar
            count={selectedIn('projects', projectItems).length}
            onRestore={() => restoreSelected('projects', selectedIn('projects', projectItems))}
            onDelete={() =>
              purgeSelected('projects', selectedIn('projects', projectItems).map((i) => i.id))
            }
          />
          <TruncationHint loaded={trash.projects.length} total={counts.projects} />
          <ul className="project-grid">
            {projects.map((project) => (
              <li key={project.id} className="trash-item">
                <input
                  type="checkbox"
                  className="trash-item-check"
                  aria-label={`Select project ${project.name}`}
                  checked={selected.projects.has(project.id)}
                  onChange={() => toggleItem('projects', project.id)}
                />
                <NoNav>
                  <ProjectCard
                    project={project}
                    actions={
                      <>
                        <DeletedAt at={project.deleted_at} />
                        {project.archived_task_count > 0 && (
                          <span className="trash-meta">
                            {project.archived_task_count} task
                            {project.archived_task_count === 1 ? '' : 's'} to restore
                          </span>
                        )}
                        <button
                          type="button"
                          aria-label={`Restore project ${project.name}`}
                          onClick={() =>
                            void restoreProjectById(
                              project.id,
                              project.name,
                              project.archived_task_count,
                            )
                          }
                        >
                          Restore
                        </button>
                        <button
                          type="button"
                          className="trash-danger"
                          aria-label={`Delete project ${project.name} forever`}
                          onClick={() => confirmPurge('projects', project.id, project.name)}
                        >
                          Delete forever
                        </button>
                      </>
                    }
                  />
                </NoNav>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!loading && tasks.length > 0 && (
        <section className="trash-section">
          <div className="trash-section-head">
            <h2>
              <Trash2 size={18} aria-hidden="true" />
              Tasks ({headingCount(tasks.length, trash.tasks.length, counts.tasks)})
            </h2>
            <SelectAll
              ids={taskItems.map((i) => i.id)}
              selected={selected.tasks}
              onChange={(select) => toggleAll('tasks', taskItems.map((i) => i.id), select)}
            />
            <button
              type="button"
              className="secondary-action"
              onClick={() => void restoreAll('tasks', taskItems)}
            >
              {restoreAllLabel(tasks.length, trash.tasks.length, counts.tasks)}
            </button>
          </div>
          <BulkBar
            count={selectedIn('tasks', taskItems).length}
            onRestore={() => restoreSelected('tasks', selectedIn('tasks', taskItems))}
            onDelete={() =>
              purgeSelected('tasks', selectedIn('tasks', taskItems).map((i) => i.id))
            }
          />
          <TruncationHint loaded={trash.tasks.length} total={counts.tasks} />
          <p className="trash-section-hint">
            Restored tasks return to their original project (or General if it was deleted).
          </p>
          <ul className="task-list">
            {tasks.map((task) => (
              <li key={task.id} className="trash-item">
                <input
                  type="checkbox"
                  className="trash-item-check"
                  aria-label={`Select task ${task.title}`}
                  checked={selected.tasks.has(task.id)}
                  onChange={() => toggleItem('tasks', task.id)}
                />
                <NoNav>
                  <TaskCard
                    task={task}
                    projects={trash.projects}
                    actions={
                      <>
                        <DeletedAt at={task.deleted_at} />
                        <button
                          type="button"
                          aria-label={`Restore task ${task.title}`}
                          onClick={() => void restoreTaskById(task.id, task.title)}
                        >
                          Restore
                        </button>
                        <button
                          type="button"
                          className="trash-danger"
                          aria-label={`Delete task ${task.title} forever`}
                          onClick={() => confirmPurge('tasks', task.id, task.title)}
                        >
                          Delete forever
                        </button>
                      </>
                    }
                  />
                </NoNav>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  )
}
