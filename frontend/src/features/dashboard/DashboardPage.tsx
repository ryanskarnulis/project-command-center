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
import { useTaskRefresh } from '../tasks/taskRefreshContext'
import type { Task, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { TaskFormModal } from '../tasks/TaskFormModal'
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

  // Signal counts cover exactly what the board can surface: root tasks filed
  // in a project. Unfiled tasks live on /tasks, not in any lane.
  const boardTasks = useMemo(
    () =>
      tasks.filter(
        (task) => task.parent_task_id === null && task.project_id !== null,
      ),
    [tasks],
  )

  // The headline describes what the lanes hold: unfiled tasks appear in no
  // lane, so lumping them into one total reads as a wrong count. Both slices
  // come from the same fetch the board renders, so they can't drift from it.
  const filedOpenCount = tasks.filter((t) => t.project_id !== null).length
  const unfiledOpenCount = tasks.length - filedOpenCount

  // Route a lane move to the right endpoint: Done uses the recurrence-safe
  // done endpoint, leaving Done reopens (→ open), everything else is a PATCH.
  async function handleSetStatus(
    task: Task,
    target: TaskWorkflowStatus,
  ): Promise<void> {
    const mutation = async () => {
      if (target === 'done') {
        await markTaskDone(task.id)
      } else if (task.workflow_status === 'done') {
        await reopenTask(task.id)
        if (target === 'in_progress') {
          await updateTask(task.id, { workflow_status: 'in_progress' })
        }
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
              {filedOpenCount} open{' '}
              {filedOpenCount === 1 ? 'task' : 'tasks'} across all projects
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
                    onClick={() => void handleReopenProject(project.id)}
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
