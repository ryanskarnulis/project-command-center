import { type FocusEvent, Fragment, type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from 'react'
import { Ellipsis, Pencil } from 'lucide-react'
import type { Project } from '../../types/project'
import type { ProjectStats } from '../../utils/projectStatus'
import { ActivityFeed } from './ActivityFeed'
import { ProjectActionSheet } from './ProjectActionSheet'
import { ProjectTabs } from './ProjectTabs'

export type SaveState = 'idle' | 'saving' | 'saved' | 'error'
export type SaveField = 'name' | 'description'
/** Inline error per field: a failed write's message, or the blank-name rule. */
export type SaveErrors = Partial<Record<SaveField, string>>

/**
 * Why a field is open. A tap ('user') takes focus. A failed write reopening the
 * field ('recovery') must not: the user may have moved on to the other field,
 * and stealing focus would blur it and commit a half-typed edit.
 */
type EditMode = false | 'user' | 'recovery'

interface Props {
  project: Project
  stats: ProjectStats
  tasksLoading: boolean
  tasksError: string | null
  nameDraft: string
  descriptionDraft: string
  onNameChange: (value: string) => void
  onDescriptionChange: (value: string) => void
  /** Commit the name draft. Resolves false when the field must stay (or return to) edit mode: a blank name, or a failed write. */
  saveName: () => Promise<boolean>
  saveDescription: () => Promise<boolean>
  /** Escape: drop the name draft back to the saved name. */
  revertName: () => void
  /** State of the newest write, shown as a transient meta-line item. */
  saveState: SaveState
  saveErrors: SaveErrors
  activityKey: number
  onToggleClosed: () => void
  onDelete: () => void
}

/** How long "Saved" stays on the meta line before it clears. */
const SAVED_VISIBLE_MS = 2000

/**
 * M06f: the phone-width project overview. It is the project's brief — name,
 * health, description, activity — and hands the work itself to the Tasks tab.
 * Name and description read as text until tapped; the lifecycle actions live
 * behind the title row's `⋯`.
 */
export function MobileProjectOverview({
  project,
  stats,
  tasksLoading,
  tasksError,
  nameDraft,
  descriptionDraft,
  onNameChange,
  onDescriptionChange,
  saveName,
  saveDescription,
  revertName,
  saveState,
  saveErrors,
  activityKey,
  onToggleClosed,
  onDelete,
}: Props) {
  const [editingName, setEditingName] = useState<EditMode>(false)
  const [editingDescription, setEditingDescription] = useState<EditMode>(false)
  const [sheetOpen, setSheetOpen] = useState(false)
  // Escape reverts the draft and unmounts the input. Chromium dispatches blur
  // for a focused element as it is removed — while it is still attached, so the
  // event reaches React with the pre-revert draft in its closure. This flag
  // makes that blur a no-op; it is re-armed each time the field opens.
  const skipNameBlur = useRef(false)
  const doneButton = useRef<HTMLButtonElement>(null)

  // "Saved" is transient on this route: it leaves the meta line after ~2s. The
  // expiry resets whenever the save state changes (a new write passes through
  // 'saving' first) — derived-state reset during render, not in an effect.
  const [seenSaveState, setSeenSaveState] = useState(saveState)
  const [savedExpired, setSavedExpired] = useState(false)
  if (seenSaveState !== saveState) {
    setSeenSaveState(saveState)
    setSavedExpired(false)
  }
  useEffect(() => {
    if (saveState !== 'saved') return
    const timer = setTimeout(() => setSavedExpired(true), SAVED_VISIBLE_MS)
    return () => clearTimeout(timer)
  }, [saveState])
  const visibleSaveState: SaveState = saveState === 'saved' && savedExpired ? 'idle' : saveState

  const closed = Boolean(project.closed_at)
  const status = closed ? { label: 'Closed', tone: 'neutral' as const } : stats.status
  const percent = Math.round(stats.progress * 100)

  // One meta line: status word first, then only the non-zero counts. While the
  // list loads there is nothing to derive from; when it failed, the alert below
  // says so and the line carries no word we cannot stand behind ("Closed" is
  // known from the project itself).
  const items: ReactNode[] = []
  if (tasksLoading) {
    if (closed) items.push(<span key="status" className="status-pill tone-neutral">Closed</span>)
    items.push(<span key="loading">Loading tasks…</span>)
  } else if (tasksError) {
    if (closed) items.push(<span key="status" className="status-pill tone-neutral">Closed</span>)
  } else {
    items.push(<span key="status" className={`status-pill tone-${status.tone}`}>{status.label}</span>)
    if (stats.open > 0) items.push(<span key="open">{stats.open} open</span>)
    if (stats.subtasks > 0) items.push(<span key="subtasks">{stats.subtasks} {stats.subtasks === 1 ? 'subtask' : 'subtasks'}</span>)
    if (stats.done > 0) items.push(<span key="done">{stats.done} done</span>)
  }
  if (visibleSaveState === 'saving') items.push(<span key="save" role="status">Saving…</span>)
  if (visibleSaveState === 'saved') items.push(<span key="save" role="status">Saved</span>)
  if (visibleSaveState === 'error') items.push(<span key="save" role="alert" className="mobile-save-failed">Not saved</span>)

  function startEditingName(): void {
    skipNameBlur.current = false
    setEditingName('user')
  }

  function commitName(): void {
    if (skipNameBlur.current) {
      skipNameBlur.current = false
      return
    }
    // A blank name keeps the field open with "Name is required" under it.
    if (!nameDraft.trim()) {
      void saveName()
      return
    }
    setEditingName(false)
    void saveName().then((ok) => { if (!ok) setEditingName('recovery') })
  }

  function handleNameKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === 'Enter') event.currentTarget.blur()
    if (event.key === 'Escape') {
      skipNameBlur.current = true
      revertName()
      setEditingName(false)
    }
  }

  function commitDescription(event: FocusEvent<HTMLTextAreaElement>): void {
    void saveDescription().then((ok) => { if (!ok) setEditingDescription('recovery') })
    // Tabbing onto Done keeps the field open until Done is pressed (otherwise the
    // button would vanish under the focus it just received); any other blur closes it.
    if (event.relatedTarget !== doneButton.current) setEditingDescription(false)
  }

  return (
    <main className="mobile-project-overview">
      <header className="mobile-project-heading">
        <div className="mobile-project-title">
          {/* The h1 stays mounted through an edit so the page never loses its heading. */}
          <h1>
            {editingName ? (
              <input
                className="task-title-input"
                aria-label="Project name"
                value={nameDraft}
                autoFocus={editingName === 'user'}
                onFocus={(event) => event.currentTarget.select()}
                onChange={(event) => onNameChange(event.target.value)}
                onBlur={commitName}
                onKeyDown={handleNameKeyDown}
              />
            ) : (
              <button type="button" className="mobile-project-name" title="Edit name" onClick={startEditingName}>
                {nameDraft}
              </button>
            )}
          </h1>
          {editingName && saveErrors.name && <p role="alert" className="error mobile-inline-error">{saveErrors.name}</p>}
          <div className="mobile-project-progress">
            {!tasksError && (
              <span
                className="dashboard-lane-progress"
                role="progressbar"
                aria-label={`${project.name} progress`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={tasksLoading ? undefined : percent}
              >
                {/* The done count can arrive first; without the list the ratio is meaningless, so paint nothing yet. */}
                <span style={{ width: `${tasksLoading ? 0 : percent}%` }} />
              </span>
            )}
            <span className="mobile-project-meta">
              {items.map((item, index) => (
                <Fragment key={index}>
                  {index > 0 && <span className="mobile-meta-sep" aria-hidden="true">·</span>}
                  {item}
                </Fragment>
              ))}
            </span>
          </div>
          {tasksError && <p role="alert" className="error mobile-inline-error">{tasksError}</p>}
        </div>
        {!project.is_protected && (
          <button
            type="button"
            className="mobile-project-actions"
            aria-label="Project actions"
            aria-haspopup="dialog"
            aria-expanded={sheetOpen}
            onClick={() => setSheetOpen(true)}
          >
            <Ellipsis size={18} aria-hidden="true" />
          </button>
        )}
      </header>

      <ProjectTabs projectId={project.id} />

      <section className="mobile-project-description" aria-labelledby="mobile-description-label">
        <div className="mobile-section-heading">
          <h2 id="mobile-description-label">Description</h2>
          {editingDescription ? (
            <button ref={doneButton} type="button" className="mobile-description-done" onClick={() => setEditingDescription(false)}>
              Done
            </button>
          ) : (
            <button type="button" className="mobile-description-edit" aria-label="Edit description" onClick={() => setEditingDescription('user')}>
              <Pencil size={15} aria-hidden="true" />
            </button>
          )}
        </div>
        {editingDescription ? (
          <>
            <textarea
              aria-label="Project description"
              value={descriptionDraft}
              placeholder="Add a description"
              rows={5}
              autoFocus={editingDescription === 'user'}
              onFocus={(event) => {
                const end = event.currentTarget.value.length
                event.currentTarget.setSelectionRange(end, end)
              }}
              onChange={(event) => onDescriptionChange(event.target.value)}
              onBlur={commitDescription}
            />
            {saveErrors.description && <p role="alert" className="error mobile-inline-error">{saveErrors.description}</p>}
          </>
        ) : (
          // The pencil is the accessible control; the paragraph is the larger
          // pointer target for the same edit.
          <p className={descriptionDraft ? 'mobile-project-body' : 'mobile-project-body empty'} onClick={() => setEditingDescription('user')}>
            {descriptionDraft || 'Add a description'}
          </p>
        )}
      </section>

      <ActivityFeed projectId={project.id} refreshKey={activityKey} />

      {sheetOpen && (
        <ProjectActionSheet
          closed={closed}
          onClose={() => setSheetOpen(false)}
          onToggleClosed={onToggleClosed}
          onDelete={onDelete}
        />
      )}
    </main>
  )
}
