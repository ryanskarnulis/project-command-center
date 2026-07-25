import { useMemo, useState } from 'react'
import { Plus } from 'lucide-react'
import { Link } from 'react-router-dom'
import { createProject, reopenProject, reorderProjects } from '../../api/projects'
import type { ProjectCreate } from '../../types/project'
import { ProjectFormModal } from '../projects/ProjectFormModal'
import {
  createUnscopedTask,
  markTaskDone,
  reopenTask,
  updateTask,
} from '../../api/tasks'
import { useToast } from '../../components/ToastContext'
import { fireAndForget } from '../../utils/async'
import { useTaskRefresh } from '../tasks/taskRefreshContext'
import type { Task, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { TaskFormModal } from '../tasks/TaskFormModal'
import { isEffectiveTopLevel } from '../tasks/taskTree'
import { TaskPanelProvider } from '../tasks/panel/TaskPanelProvider'
import { DashboardSignalStrip } from './DashboardSignalStrip'
import { DashboardSwimlaneBoard } from './DashboardSwimlaneBoard'
import type { DashboardSignal } from './dashboardSignals'
import { useDashboard } from './useDashboard'

export function DashboardPage() {
  const {
    overview,
    tasks,
    projects,
    closedProjects,
    loading,
    refreshing,
    error,
    reload,
  } = useDashboard()
  const { withToast } = useToast()
  const { bump: bumpTaskRefresh } = useTaskRefresh()
  const [signal, setSignal] = useState<DashboardSignal | null>(null)
  const [creatingProject, setCreatingProject] = useState(false)
  const [addingTask, setAddingTask] = useState(false)

  // The lanes are exactly the backend's non-closed projects (get_overview
  // filters closed_at). Scoping counts to these ids ties the headline and
  // signals to the rendered board: a task in a closed project has no lane, so
  // it must not be counted here either.
  const laneProjectIds = useMemo(
    () => new Set((overview?.projects ?? []).map((p) => p.project_id)),
    [overview],
  )

  // Signal counts cover exactly what the board can surface: root tasks filed
  // in a currently-shown (non-closed) project. "Root" is the effective rule
  // (isEffectiveTopLevel), matching the lanes — an orphan promoted by its
  // parent's deletion is a card, so it must be counted as one. Unfiled tasks live on /tasks,
  // and closed-project tasks live in no lane — neither belongs in a signal.
  const boardTasks = useMemo(
    () =>
      tasks.filter(
        (task) =>
          isEffectiveTopLevel(task) &&
          task.project_id !== null &&
          laneProjectIds.has(task.project_id),
      ),
    [tasks, laneProjectIds],
  )

  // The headline describes what the lanes hold: unfiled tasks appear in no lane
  // (counted separately as a /tasks link) and closed-project tasks in none at
  // all, so both are excluded from the filed total the board renders. Subtasks
  // are called out separately rather than folded into "open tasks" (same rule
  // as the lane headers) — a count larger than the visible cards reads wrong.
  const filedOpenCount = tasks.filter(
    (t) => t.project_id !== null && laneProjectIds.has(t.project_id),
  ).length
  const filedOpenRootCount = boardTasks.length
  const filedOpenSubtaskCount = filedOpenCount - filedOpenRootCount
  const unfiledOpenCount = tasks.filter((t) => t.project_id === null).length

  // Route a lane move to the right endpoint: Done uses the recurrence-safe
  // done endpoint, Done → Open reopens, everything else (including
  // Done → In progress) is a single PATCH.
  async function handleSetStatus(
    task: Task,
    target: TaskWorkflowStatus,
  ): Promise<void> {
    const mutation = async () => {
      if (target === 'done') {
        await markTaskDone(task.id)
      } else if (task.workflow_status === 'done' && target !== 'in_progress') {
        await reopenTask(task.id)
      } else if (task.workflow_status === 'done') {
        // Done → In progress is a single atomic PATCH; the old
        // reopen-then-patch pair could half-commit as Open. (#148)
        await updateTask(task.id, { workflow_status: 'in_progress' })
      } else {
        await updateTask(task.id, { workflow_status: target })
      }
    }
    await withToast(mutation(), { success: 'Task status updated' })
    reload()
  }

  async function handleUpdate(task: Task, patch: TaskUpdate): Promise<void> {
    await withToast(updateTask(task.id, patch), { success: 'Task saved' })
    reload()
  }

  async function handleReorder(projectIds: number[]): Promise<void> {
    await withToast(reorderProjects(projectIds), {
      success: 'Projects reordered',
    })
    bumpTaskRefresh()
  }

  async function handleReopenProject(projectId: number): Promise<void> {
    await withToast(reopenProject(projectId), { success: 'Project reopened' })
    reload()
    bumpTaskRefresh()
  }

  async function handleCreateProject(data: ProjectCreate): Promise<void> {
    await withToast(createProject(data), { success: 'Project created' })
    reload()
    bumpTaskRefresh()
  }

  if (loading) {
    return (
      <div className="dashboard">
        <div className="page-loading">Loading dashboard...</div>
      </div>
    )
  }
  if (error) {
    return (
      <div className="dashboard">
        <p role="alert" className="error">
          Error: {error}
        </p>
      </div>
    )
  }
  if (!overview) return null

  return (
    <TaskPanelProvider onMutated={reload}>
      <div className="dashboard" aria-busy={refreshing}>
        <div className="dashboard-board-heading">
          <div>
            <h1>Project board</h1>
            <p>
              {filedOpenRootCount} open{' '}
              {filedOpenRootCount === 1 ? 'task' : 'tasks'}
              {filedOpenSubtaskCount > 0 &&
                ` with ${filedOpenSubtaskCount} ${
                  filedOpenSubtaskCount === 1 ? 'subtask' : 'subtasks'
                }`}{' '}
              across all projects
              {unfiledOpenCount > 0 && (
                <>
                  {' · '}
                  <Link to="/tasks">{unfiledOpenCount} unfiled</Link>
                </>
              )}
            </p>
          </div>
          <div className="dashboard-board-actions">
            <button
              type="button"
              className="dashboard-add-task"
              onClick={() => setCreatingProject(true)}
            >
              <Plus size={16} aria-hidden="true" />
              New project
            </button>
            <button
              type="button"
              className="dashboard-add-task"
              onClick={() => setAddingTask(true)}
            >
              <Plus size={16} aria-hidden="true" />
              Add task
            </button>
          </div>
        </div>

        <DashboardSignalStrip
          tasks={boardTasks}
          activeSignal={signal}
          onChange={setSignal}
        />

        <DashboardSwimlaneBoard
          projects={overview.projects}
          tasks={tasks}
          signal={signal}
          onSetStatus={handleSetStatus}
          onUpdate={handleUpdate}
          onReorder={handleReorder}
          onCreateProject={() => setCreatingProject(true)}
        />

        {closedProjects.length > 0 && (
          <details className="dashboard-closed-projects">
            <summary>Closed projects ({closedProjects.length})</summary>
            <ul>
              {closedProjects.map((project) => (
                <li key={project.id}>
                  <Link to={`/projects/${project.id}`}>{project.name}</Link>
                  <button
                    type="button"
                    onClick={() => fireAndForget(handleReopenProject(project.id))}
                  >
                    Reopen
                  </button>
                </li>
              ))}
            </ul>
          </details>
        )}

        {creatingProject && (
          <ProjectFormModal
            mode="create"
            onClose={() => setCreatingProject(false)}
            onSave={handleCreateProject}
          />
        )}

        {addingTask && (
          <TaskFormModal
            mode="create"
            tasks={tasks}
            projects={projects}
            onClose={() => setAddingTask(false)}
            onSave={async (data) => {
              await withToast(createUnscopedTask(data), {
                success: 'Task created',
              })
              reload()
            }}
          />
        )}
      </div>
    </TaskPanelProvider>
  )
}
