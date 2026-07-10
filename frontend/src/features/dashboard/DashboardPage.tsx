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
  const rootTasks = useMemo(
    () => tasks.filter((task) => task.parent_task_id === null),
    [tasks],
  )

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
    return <main className="dashboard"><div className="page-loading">Loading dashboard…</div></main>
  }
  if (error) {
    return <main className="dashboard"><p role="alert" className="error">Error: {error}</p></main>
  }
  if (!overview) return null

  return (
    <TaskPanelProvider onMutated={reload}>
      <main className="dashboard" aria-busy={refreshing}>
        <div className="dashboard-board-heading">
          <h1>Project board</h1>
          <Link to="/tasks?new=1" className="dashboard-add-task">
            <Plus size={16} aria-hidden="true" />
            Add task
          </Link>
        </div>

        <DashboardSignalStrip
          tasks={rootTasks}
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
      </main>
    </TaskPanelProvider>
  )
}
