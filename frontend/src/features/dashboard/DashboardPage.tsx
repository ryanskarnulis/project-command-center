import { useMemo, useState } from 'react'
import { Plus } from 'lucide-react'
import { Link } from 'react-router-dom'
import { markTaskDone, reopenTask, updateTask } from '../../api/tasks'
import { useToast } from '../../components/ToastContext'
import type { Task, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { TaskPanelProvider } from '../tasks/panel/TaskPanelProvider'
import { DashboardSignalStrip } from './DashboardSignalStrip'
import { DashboardSwimlaneBoard } from './DashboardSwimlaneBoard'
import type { DashboardSignal } from './dashboardSignals'
import { useDashboard } from './useDashboard'

export function DashboardPage() {
  const { overview, tasks, loading, refreshing, error, reload } = useDashboard()
  const { withToast } = useToast()
  const [signal, setSignal] = useState<DashboardSignal | null>(null)

  // Signal counts cover exactly what the board can surface: root tasks filed
  // in a project. Unfiled tasks live on /tasks, not in any lane.
  const boardTasks = useMemo(
    () =>
      tasks.filter(
        (task) => task.parent_task_id === null && task.project_id !== null,
      ),
    [tasks],
  )

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
            <p>{overview.total_open_tasks} open tasks across all projects</p>
          </div>
          <Link to="/tasks?new=1" className="dashboard-add-task">
            <Plus size={16} aria-hidden="true" />
            Add task
          </Link>
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
        />
      </div>
    </TaskPanelProvider>
  )
}
