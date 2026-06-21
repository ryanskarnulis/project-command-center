import { useMemo, useState, type ReactNode } from 'react'
import { FolderX, GraduationCap, Inbox, Search, SlidersHorizontal, Trash2 } from 'lucide-react'
import { useTrash, type TrashKind } from './useTrash'
import { useTrashCount } from './trashCountContext'
import { TaskCard } from '../tasks/TaskCard'
import { ProjectCard } from '../projects/ProjectCard'
import { buildProjectStats, type ProjectStats } from '../../utils/projectStatus'
import { formatRelative } from '../../utils/dates'

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

type TypeFilter = 'all' | 'projects' | 'tasks' | 'inbox' | 'training'

// An inbox item's display label: the summary if the model produced one, else a
// snippet of the raw text (matches what the inbox section renders).
function inboxLabel(item: { summary: string | null; raw_text: string }): string {
  return item.summary ?? item.raw_text.slice(0, 60)
}

// A training example's display label: a snippet of the input that produced it.
function trainingLabel(example: { input_text: string }): string {
  return example.input_text.slice(0, 80)
}

export function TrashPage() {
  const {
    trash,
    loading,
    error,
    notice,
    restoreProjectById,
    restoreTaskById,
    restoreInboxById,
    restoreTrainingById,
    restoreAll,
    purgeById,
    emptyTrashAll,
  } = useTrash()
  // True per-kind totals, unbounded by the /trash list page (see /trash/count).
  const { counts } = useTrashCount()

  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')

  // Trashed projects' tasks are cascade-soft-deleted too, so derive each card's
  // stats from the trashed task set grouped by project.
  const statsByProject = useMemo(() => {
    const openTasks = new Map<number, typeof trash.tasks>()
    const doneCount = new Map<number, number>()
    for (const t of trash.tasks) {
      if (t.project_id === null) continue
      if (t.workflow_status === 'done') {
        doneCount.set(t.project_id, (doneCount.get(t.project_id) ?? 0) + 1)
      } else {
        const arr = openTasks.get(t.project_id) ?? []
        arr.push(t)
        openTasks.set(t.project_id, arr)
      }
    }
    const map = new Map<number, ProjectStats>()
    for (const p of trash.projects) {
      map.set(p.id, buildProjectStats(openTasks.get(p.id) ?? [], doneCount.get(p.id) ?? 0))
    }
    return map
  }, [trash])

  const totalCount =
    trash.projects.length +
    trash.tasks.length +
    trash.inbox_items.length +
    trash.training_examples.length
  const isEmpty = totalCount === 0

  // Purge is irreversible (it really deletes the row), so every purge path is
  // gated by an explicit confirm naming what's about to go.
  const confirmPurge = (kind: TrashKind, id: number, label: string) => {
    if (window.confirm(`Permanently delete “${label}”? This cannot be undone.`)) {
      void purgeById(kind, id, label)
    }
  }

  const confirmEmptyTrash = () => {
    if (
      window.confirm(
        `Permanently delete all ${totalCount} item${totalCount === 1 ? '' : 's'} in trash? This cannot be undone.`,
      )
    ) {
      void emptyTrashAll()
    }
  }

  // Search (case-insensitive, over each item's display label) + type filter are
  // both client-side. A list is included only when the type filter selects it;
  // the search then narrows within whatever's included.
  const { projects, tasks, inboxItems, trainingExamples } = useMemo(() => {
    const q = search.trim().toLowerCase()
    const showProjects = typeFilter === 'all' || typeFilter === 'projects'
    const showTasks = typeFilter === 'all' || typeFilter === 'tasks'
    const showInbox = typeFilter === 'all' || typeFilter === 'inbox'
    const showTraining = typeFilter === 'all' || typeFilter === 'training'
    const match = (label: string) => q === '' || label.toLowerCase().includes(q)
    return {
      projects: showProjects ? trash.projects.filter((p) => match(p.name)) : [],
      tasks: showTasks ? trash.tasks.filter((t) => match(t.title)) : [],
      inboxItems: showInbox ? trash.inbox_items.filter((i) => match(inboxLabel(i))) : [],
      trainingExamples: showTraining
        ? trash.training_examples.filter(
            (e) => match(e.task_name) || match(trainingLabel(e)),
          )
        : [],
    }
  }, [trash, search, typeFilter])

  const filtersActive = search.trim() !== '' || typeFilter !== 'all'
  const noMatches =
    !isEmpty &&
    projects.length === 0 &&
    tasks.length === 0 &&
    inboxItems.length === 0 &&
    trainingExamples.length === 0

  // The number shown next to a section title. While filtering, the filtered
  // length is what's honest (it matches the cards shown). Otherwise show the true
  // total, falling back to the loaded length if the count hasn't arrived (or no
  // TrashCountProvider is mounted), so the heading never reads lower than what's
  // on screen.
  const headingCount = (filtered: number, loaded: number, total: number) =>
    filtersActive ? filtered : Math.max(total, loaded)

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
                placeholder="Project, task, or note text"
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
                <option value="inbox">Inbox</option>
                <option value="training">Training</option>
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
            <button
              type="button"
              className="secondary-action"
              onClick={() =>
                void restoreAll(
                  'projects',
                  projects.map((p) => ({ id: p.id, label: p.name })),
                )
              }
            >
              Restore all
            </button>
          </div>
          <TruncationHint loaded={trash.projects.length} total={counts.projects} />
          <ul className="project-grid">
            {projects.map((project) => (
              <li key={project.id}>
                <NoNav>
                  <ProjectCard
                    project={project}
                    stats={statsByProject.get(project.id)}
                    actions={
                      <>
                        <DeletedAt at={project.deleted_at} />
                        <button
                          type="button"
                          aria-label={`Restore project ${project.name}`}
                          onClick={() => void restoreProjectById(project.id, project.name)}
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
            <button
              type="button"
              className="secondary-action"
              onClick={() =>
                void restoreAll(
                  'tasks',
                  tasks.map((t) => ({ id: t.id, label: t.title })),
                )
              }
            >
              Restore all
            </button>
          </div>
          <TruncationHint loaded={trash.tasks.length} total={counts.tasks} />
          <p className="trash-section-hint">
            Restored tasks return to their original project (or General if it was deleted).
          </p>
          <ul className="task-list">
            {tasks.map((task) => (
              <li key={task.id}>
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

      {!loading && inboxItems.length > 0 && (
        <section className="trash-section">
          <div className="trash-section-head">
            <h2>
              <Inbox size={18} aria-hidden="true" />
              Inbox items ({headingCount(inboxItems.length, trash.inbox_items.length, counts.inbox_items)})
            </h2>
            <button
              type="button"
              className="secondary-action"
              onClick={() =>
                void restoreAll(
                  'inbox',
                  inboxItems.map((i) => ({ id: i.id, label: inboxLabel(i) })),
                )
              }
            >
              Restore all
            </button>
          </div>
          <TruncationHint loaded={trash.inbox_items.length} total={counts.inbox_items} />
          <ul className="task-list">
            {inboxItems.map((item) => {
              const label = inboxLabel(item)
              return (
                <li key={item.id}>
                  <div className="task-card">
                    <div className="task-card-body">
                      <span className="task-card-title">{label}</span>
                      <div className="task-card-badges">
                        <span className="source-pill">{item.source}</span>
                      </div>
                    </div>
                    <div className="task-card-actions">
                      <DeletedAt at={item.deleted_at} />
                      <button
                        type="button"
                        aria-label={`Restore inbox item ${label}`}
                        onClick={() => void restoreInboxById(item.id, label)}
                      >
                        Restore
                      </button>
                      <button
                        type="button"
                        className="trash-danger"
                        aria-label={`Delete inbox item ${label} forever`}
                        onClick={() => confirmPurge('inbox', item.id, label)}
                      >
                        Delete forever
                      </button>
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {!loading && trainingExamples.length > 0 && (
        <section className="trash-section">
          <div className="trash-section-head">
            <h2>
              <GraduationCap size={18} aria-hidden="true" />
              Training examples ({headingCount(trainingExamples.length, trash.training_examples.length, counts.training_examples)})
            </h2>
            <button
              type="button"
              className="secondary-action"
              onClick={() =>
                void restoreAll(
                  'training',
                  trainingExamples.map((e) => ({ id: e.id, label: trainingLabel(e) })),
                )
              }
            >
              Restore all
            </button>
          </div>
          <TruncationHint
            loaded={trash.training_examples.length}
            total={counts.training_examples}
          />
          <ul className="task-list">
            {trainingExamples.map((example) => {
              const label = trainingLabel(example)
              return (
                <li key={example.id}>
                  <div className="task-card">
                    <div className="task-card-body">
                      <span className="task-card-title">{label}</span>
                      <div className="task-card-badges">
                        <span className="source-pill">{example.task_name}</span>
                        <span className="source-pill">{example.model_name}</span>
                      </div>
                    </div>
                    <div className="task-card-actions">
                      <DeletedAt at={example.deleted_at} />
                      <button
                        type="button"
                        aria-label={`Restore training example ${label}`}
                        onClick={() => void restoreTrainingById(example.id, label)}
                      >
                        Restore
                      </button>
                      <button
                        type="button"
                        className="trash-danger"
                        aria-label={`Delete training example ${label} forever`}
                        onClick={() => confirmPurge('training', example.id, label)}
                      >
                        Delete forever
                      </button>
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        </section>
      )}
    </main>
  )
}
