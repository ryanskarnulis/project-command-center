import { useMemo } from 'react'
import { AsyncState } from '../../components/AsyncState'
import type { Project } from '../../types/project'
import type { Task, TaskWorkflowStatus } from '../../types/task'
import { KanbanBoard } from './KanbanBoard'
import { isActive, matchesFilters, type Filters } from './taskFilters'

interface TaskBoardViewProps {
  tasks: Task[]
  completedTasks: Task[]
  filters: Filters
  projects: Project[]
  isGlobal: boolean
  loading: boolean
  error: string | null
  completedLoading: boolean
  completedError: string | null
  filtersActive: boolean
  onSetStatus: (t: Task, target: TaskWorkflowStatus) => Promise<void>
}

export function TaskBoardView({
  tasks,
  completedTasks,
  filters,
  projects,
  isGlobal,
  loading,
  error,
  completedLoading,
  completedError,
  filtersActive,
  onSetStatus,
}: TaskBoardViewProps) {
  // The board lays tasks out by workflow_status across three columns, so the
  // Status filter doesn't apply — keep every other filter, drop status.
  const boardFilters = useMemo(
    () => ({ ...filters, status: '' as const }),
    [filters],
  )
  // The board is a flat layout with no nesting affordance, so it shows only
  // root tasks — subtasks (parent_task_id !== null) are excluded entirely.
  const boardActive = useMemo(() => {
    const source = isActive(boardFilters)
      ? tasks.filter((t) => matchesFilters(t, boardFilters))
      : tasks
    return source.filter((t) => t.parent_task_id === null)
  }, [tasks, boardFilters])
  const boardDone = useMemo(
    () =>
      completedTasks.filter(
        (t) => matchesFilters(t, boardFilters) && t.parent_task_id === null,
      ),
    [completedTasks, boardFilters],
  )

  return (
    <AsyncState
      loading={loading || completedLoading}
      error={error ?? completedError}
      isEmpty={boardActive.length === 0 && boardDone.length === 0}
      emptyLabel={
        filtersActive
          ? 'No tasks match the current filters.'
          : 'No tasks yet.'
      }
    >
      <KanbanBoard
        activeTasks={boardActive}
        completedTasks={boardDone}
        projects={projects}
        isGlobal={isGlobal}
        onSetStatus={onSetStatus}
      />
    </AsyncState>
  )
}
