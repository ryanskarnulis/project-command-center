import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Columns3, List } from 'lucide-react'
import { listProjects } from '../../api/projects'
import { createUnscopedTask } from '../../api/tasks'
import { useToast } from '../../components/ToastContext'
import { fireAndForget } from '../../utils/async'
import type { Project } from '../../types/project'
import type { Task, TaskCreate, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { ActivityFeed } from '../projects/ActivityFeed'
import { ProjectTabs } from '../projects/ProjectTabs'
import { QuickAddBar } from './quickadd/QuickAddBar'
import { TaskFormModal } from './TaskFormModal'
import { TaskFilters } from './TaskFilters'
import { TaskListView } from './TaskListView'
import { TaskBoardView } from './TaskBoardView'
import { SkipOccurrenceConfirm } from './SkipOccurrenceConfirm'
import { TaskPanelProvider } from './panel/TaskPanelProvider'
import { useCompletedTasks } from './useCompletedTasks'
import { useTaskUrlState } from './useTaskUrlState'
import { useTasks } from './useTasks'
import { useMobileTasks } from './useMobileTasks'
import { MobileProjectTasks } from './mobile/MobileProjectTasks'

/** The per-project task surface: `/projects/:projectId/tasks`, one of two tabs. */
export function TasksPage() {
  // Always project-scoped — the cross-project `/tasks` route was retired, and
  // `RequireRouteId` guarantees a positive integer before this renders.
  const { projectId } = useParams()
  const id = Number(projectId)
  const mobile = useMobileTasks()
  const { tasks, loading, error, create, update, markDone, skip, remove, reload } =
    useTasks(id)
  // The recurring task whose skip is awaiting confirmation (null = no dialog).
  const [skipTarget, setSkipTarget] = useState<Task | null>(null)

  const {
    view,
    addingTask,
    filters,
    sortMode,
    filtersActive,
    hasNonStatusFilters,
    activeFilterCount,
    updateTaskQuery,
    selectView,
  } = useTaskUrlState('board')
  const [projects, setProjects] = useState<Project[]>([])

  // Desktop lazily loads the archive for its Done view/column. Mobile also
  // needs it for progress and the filter sheet's live result count.
  const showingCompleted = filters.status === 'done'
  const {
    tasks: completedTasks,
    loading: completedLoading,
    error: completedError,
    reopen,
    reload: reloadCompleted,
  } = useCompletedTasks(id, mobile || showingCompleted || view === 'board')

  // "More options" hands an in-progress draft (quick-add or subtask composer)
  // to the full task modal.
  const [draftModalDefaults, setDraftModalDefaults] =
    useState<Partial<TaskCreate> | null>(null)

  useEffect(() => {
    // Closed projects are included so the "Project" sort key can still name a
    // task filed in one (#133); they are dropped from the filing targets below.
    listProjects(true).then(setProjects).catch(() => {})
  }, [])

  // Closed projects identify existing tasks but are not filing targets: they
  // stay out of quick-add's #project tokens and the create modal's picker.
  const openProjects = useMemo(
    () => projects.filter((p) => !p.closed_at),
    [projects],
  )

  const [activityKey, setActivityKey] = useState(0)
  const bumpActivity = () => setActivityKey((k) => k + 1)

  const { withToast } = useToast()

  // Quick-add goes through the unscoped endpoint so a #project token can file
  // anywhere; the payload carries the page's project when no token is present.
  async function quickCreate(data: TaskCreate) {
    await withToast(createUnscopedTask(data), { success: 'Task created' })
    reload()
    bumpActivity()
  }

  // Route a board move to the right endpoint: Done uses the recurrence-safe
  // done endpoint, Done → Open uses reopen, everything else (including
  // Done → In progress) is a single PATCH.
  async function handleSetStatus(t: Task, target: TaskWorkflowStatus) {
    if (target === 'done') {
      await markDone(t.id)
      reloadCompleted()
    } else if (t.workflow_status === 'done') {
      if (target === 'in_progress') {
        // One write, not reopen-then-patch: splitting it left the task Open
        // when the second request failed. (#148)
        await update(t.id, { workflow_status: 'in_progress' })
        // The card leaves the Done column, which is served by the completed
        // archive — reopen() pruned it locally, a PATCH needs a refetch.
        reloadCompleted()
      } else {
        await reopen(t.id)
        reload()
        // Reopening a child can also reopen its completed parent. Pruning
        // only the child locally leaves that parent and the done count stale.
        reloadCompleted()
      }
    } else {
      await update(t.id, { workflow_status: target })
    }
    bumpActivity()
  }

  // Inline chip edits from cards. Recurring tasks get no scope prompt here —
  // an unscoped PATCH edits just this occurrence; series edits live in the panel.
  async function handleUpdate(t: Task, patch: TaskUpdate) {
    await update(t.id, patch)
    // A done-column card can be edited on the board; keep the archive fresh.
    if (t.workflow_status === 'done') reloadCompleted()
    bumpActivity()
  }

  return (
    <TaskPanelProvider
      onMutated={() => {
        reload()
        reloadCompleted()
        bumpActivity()
      }}
    >
    <main className={mobile ? 'mobile-project-tasks' : undefined}>
      {mobile ? (
        <MobileProjectTasks
          key={id}
          projectId={id}
          projects={projects}
          tasks={tasks}
          completedTasks={completedTasks}
          completedLoading={completedLoading}
          completedError={completedError}
          loading={loading}
          error={error}
          filters={filters}
          sortMode={sortMode}
          activityKey={activityKey}
          updateQuery={updateTaskQuery}
          onSetStatus={handleSetStatus}
        />
      ) : <>
      <p>
        <Link to={`/projects/${id}`}>← Project</Link>
      </p>
      <h1>Tasks</h1>
      <ProjectTabs projectId={id} />

      <div className="task-toolbar">
        <QuickAddBar
          projects={openProjects}
          scopeProjectId={id}
          onCreate={quickCreate}
          onMoreOptions={setDraftModalDefaults}
        />
        <div
          className="view-toggle"
          role="group"
          aria-label="View mode"
        >
          <button
            type="button"
            className={view === 'list' ? 'selected' : ''}
            aria-pressed={view === 'list'}
            onClick={() => selectView('list')}
          >
            <List size={16} aria-hidden="true" />
            List
          </button>
          <button
            type="button"
            className={view === 'board' ? 'selected' : ''}
            aria-pressed={view === 'board'}
            onClick={() => selectView('board')}
          >
            <Columns3 size={16} aria-hidden="true" />
            Board
          </button>
        </div>
      </div>

      <TaskFilters
        filters={filters}
        sortMode={sortMode}
        view={view}
        filtersActive={filtersActive}
        activeFilterCount={activeFilterCount}
        updateTaskQuery={updateTaskQuery}
      />

      {view === 'board' ? (
        <TaskBoardView
          tasks={tasks}
          completedTasks={completedTasks}
          filters={filters}
          loading={loading}
          error={error}
          completedLoading={completedLoading}
          completedError={completedError}
          filtersActive={filtersActive}
          onSetStatus={handleSetStatus}
          onUpdate={handleUpdate}
        />
      ) : (
        <TaskListView
          tasks={tasks}
          completedTasks={completedTasks}
          filters={filters}
          sortMode={sortMode}
          projects={projects}
          showingCompleted={showingCompleted}
          loading={loading}
          error={error}
          completedLoading={completedLoading}
          completedError={completedError}
          filtersActive={filtersActive}
          hasNonStatusFilters={hasNonStatusFilters}
          create={create}
          markDone={markDone}
          update={handleUpdate}
          onSetStatus={handleSetStatus}
          onSkip={setSkipTarget}
          remove={remove}
          reopen={reopen}
          reload={reload}
          bumpActivity={bumpActivity}
          onOpenSubtaskModal={setDraftModalDefaults}
        />
      )}

      <ActivityFeed projectId={id} refreshKey={activityKey} />
      </>}

      <SkipOccurrenceConfirm
        taskTitle={skipTarget?.title ?? null}
        onCancel={() => setSkipTarget(null)}
        onConfirm={() => {
          if (skipTarget) fireAndForget(skip(skipTarget.id).then(bumpActivity))
          setSkipTarget(null)
        }}
      />

      {addingTask && (
        <TaskFormModal
          mode="create"
          defaults={{ project_id: id }}
          tasks={tasks}
          projects={openProjects}
          onClose={() => updateTaskQuery({ addingTask: false })}
          onSave={async (data) => {
            await create(data)
            bumpActivity()
          }}
        />
      )}

      {draftModalDefaults && (
        <TaskFormModal
          mode="create"
          defaults={
            // A draft without its own project (no #project token) pre-selects
            // the page's project, matching where quick-add would file it.
            draftModalDefaults.project_id === undefined
              ? { ...draftModalDefaults, project_id: id }
              : draftModalDefaults
          }
          tasks={tasks}
          projects={openProjects}
          onClose={() => setDraftModalDefaults(null)}
          onSave={async (data) => {
            await create(data)
            bumpActivity()
          }}
        />
      )}
    </main>
    </TaskPanelProvider>
  )
}
