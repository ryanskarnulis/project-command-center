import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Columns3, List } from 'lucide-react'
import { listProjects } from '../../api/projects'
import { createUnscopedTask } from '../../api/tasks'
import { useToast } from '../../components/ToastContext'
import type { Project } from '../../types/project'
import type { Task, TaskCreate, TaskWorkflowStatus } from '../../types/task'
import { ActivityFeed } from '../projects/ActivityFeed'
import { ProjectTabs } from '../projects/ProjectTabs'
import { QuickAddBar } from './quickadd/QuickAddBar'
import { TaskFormModal } from './TaskFormModal'
import { TaskFilters } from './TaskFilters'
import { TaskListView } from './TaskListView'
import { TaskBoardView } from './TaskBoardView'
import { TaskPanelProvider } from './panel/TaskPanelProvider'
import { useCompletedTasks } from './useCompletedTasks'
import { useTaskUrlState } from './useTaskUrlState'
import { useTasks } from './useTasks'

export function TasksPage() {
  const { projectId } = useParams()
  const id = projectId === undefined ? undefined : Number(projectId)
  const isGlobal = id === undefined
  const { tasks, loading, error, create, update, markDone, remove, reload } =
    useTasks(id)

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
  } = useTaskUrlState()
  const [projects, setProjects] = useState<Project[]>([])

  // "Done" swaps the list to the completed archive (lazily fetched); the board
  // always needs it for its Done column.
  const showingCompleted = filters.status === 'done'
  const {
    tasks: completedTasks,
    loading: completedLoading,
    error: completedError,
    reopen,
    reload: reloadCompleted,
  } = useCompletedTasks(id, showingCompleted || view === 'board')

  // "More options" hands an in-progress draft (quick-add or subtask composer)
  // to the full task modal.
  const [draftModalDefaults, setDraftModalDefaults] =
    useState<Partial<TaskCreate> | null>(null)

  useEffect(() => {
    listProjects().then(setProjects).catch(() => {})
  }, [])

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
  // done endpoint, leaving Done uses reopen (→ open), everything else is a PATCH.
  async function handleSetStatus(t: Task, target: TaskWorkflowStatus) {
    if (target === 'done') {
      await markDone(t.id)
      reloadCompleted()
    } else if (t.workflow_status === 'done') {
      await reopen(t.id)
      if (target === 'in_progress') {
        await update(t.id, { workflow_status: 'in_progress' })
      } else {
        reload()
      }
    } else {
      await update(t.id, { workflow_status: target })
    }
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
    <main>
      {!isGlobal && (
        <p>
          <Link to="/projects">← Projects</Link>
        </p>
      )}
      <h1>{isGlobal ? 'Open Tasks' : 'Tasks'}</h1>
      {!isGlobal && id !== undefined && <ProjectTabs projectId={id} />}

      <div className="task-toolbar">
        <QuickAddBar
          projects={projects}
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
        isGlobal={isGlobal}
        projects={projects}
        filtersActive={filtersActive}
        activeFilterCount={activeFilterCount}
        updateTaskQuery={updateTaskQuery}
      />

      {view === 'board' ? (
        <TaskBoardView
          tasks={tasks}
          completedTasks={completedTasks}
          filters={filters}
          projects={projects}
          isGlobal={isGlobal}
          loading={loading}
          error={error}
          completedLoading={completedLoading}
          completedError={completedError}
          filtersActive={filtersActive}
          onSetStatus={handleSetStatus}
        />
      ) : (
        <TaskListView
          tasks={tasks}
          completedTasks={completedTasks}
          filters={filters}
          sortMode={sortMode}
          projects={projects}
          isGlobal={isGlobal}
          showingCompleted={showingCompleted}
          loading={loading}
          error={error}
          completedLoading={completedLoading}
          completedError={completedError}
          filtersActive={filtersActive}
          hasNonStatusFilters={hasNonStatusFilters}
          create={create}
          markDone={markDone}
          remove={remove}
          reopen={reopen}
          reload={reload}
          bumpActivity={bumpActivity}
          onOpenSubtaskModal={setDraftModalDefaults}
        />
      )}

      {!isGlobal && <ActivityFeed projectId={id} refreshKey={activityKey} />}

      {addingTask && (
        <TaskFormModal
          mode="create"
          tasks={tasks}
          projects={projects}
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
          defaults={draftModalDefaults}
          tasks={tasks}
          projects={projects}
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
