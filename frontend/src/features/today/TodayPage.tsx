import { useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, CalendarClock, Check, Clock3, Inbox, Play } from 'lucide-react'
import { markTaskDone, updateTask } from '../../api/tasks'
import { useToast } from '../../components/ToastContext'
import type { TaskPriority, TaskWorkflowStatus } from '../../types/task'
import type {
  BlockedTask,
  DueSignal,
  OverflowTask,
  ScheduledBlock,
} from '../../types/today'
import { formatDuration } from '../../utils/duration'
import { formatDueDate } from '../../utils/dates'
import {
  DEFAULT_AVAILABLE_MINUTES,
  DEFAULT_START_TIME,
  useTodayPlan,
} from './useTodayPlan'

// Capacity presets keep the control daily-scannable while staying inside the
// backend's 15–1440 bound. The current value is added if it isn't a preset so a
// deep-linked or odd value still renders.
const CAPACITY_PRESETS = [120, 240, 360, 480, 600]

const DUE_SIGNAL_LABEL: Record<DueSignal, string> = {
  overdue: 'Overdue',
  due_today: 'Due today',
  due_soon: 'Due soon',
  none: '',
}

// Maps the scheduler's due signal onto the shared .due-* pill classes.
const DUE_SIGNAL_CLASS: Record<DueSignal, string> = {
  overdue: 'due-overdue',
  due_today: 'due-today',
  due_soon: 'due-soon',
  none: 'due-none',
}

function PriorityPill({ priority }: { priority: TaskPriority }) {
  return <span className={`priority-pill priority-${priority}`}>{priority}</span>
}

function DueSignalPill({ signal }: { signal: DueSignal }) {
  if (signal === 'none') return null
  return <span className={`due ${DUE_SIGNAL_CLASS[signal]}`}>{DUE_SIGNAL_LABEL[signal]}</span>
}

function WorkflowPill({ status }: { status: TaskWorkflowStatus }) {
  return (
    <span className={`status-pill workflow-${status}`}>{status.replace('_', ' ')}</span>
  )
}

function EstimateLabel({
  minutes,
  assumed,
}: {
  minutes: number
  assumed: boolean
}) {
  return (
    <span className="today-estimate">
      <Clock3 size={13} aria-hidden="true" />
      {formatDuration(minutes)}
      {assumed && (
        <span className="today-assumed" title="No estimate on the task; assumed default">
          assumed
        </span>
      )}
    </span>
  )
}

// In-row Start / Mark done actions. Reuses the existing task endpoints and asks
// the parent to refetch on success so the row re-ranks (Start) or drops out
// (done). Mark done MUST go through the dedicated done endpoint so recurrence's
// next-occurrence creation still fires — never a raw PATCH workflow_status=done.
function TodayRowActions({
  taskId,
  title,
  workflowStatus,
  onMutated,
}: {
  taskId: number
  title: string
  workflowStatus: TaskWorkflowStatus
  onMutated: () => void
}) {
  const { withToast } = useToast()
  const [pending, setPending] = useState(false)

  async function run(action: () => Promise<unknown>, success: string): Promise<void> {
    if (pending) return
    setPending(true)
    try {
      await withToast(action(), { success })
      // Success re-ranks or removes this row; refetch unmounts it, so we leave
      // `pending` set rather than touch state on a tree that's going away.
      onMutated()
    } catch {
      // The error toast already surfaced; keep the row interactive to retry.
      setPending(false)
    }
  }

  return (
    <div className="today-row-actions">
      {workflowStatus !== 'in_progress' && (
        <button
          type="button"
          className="task-action"
          disabled={pending}
          aria-label={`Start ${title}`}
          onClick={() =>
            void run(
              () => updateTask(taskId, { workflow_status: 'in_progress' }),
              'Task started',
            )
          }
        >
          <Play size={15} aria-hidden="true" />
          Start
        </button>
      )}
      <button
        type="button"
        className="task-action"
        disabled={pending}
        aria-label={`Mark ${title} done`}
        onClick={() => void run(() => markTaskDone(taskId), 'Task marked done')}
      >
        <Check size={15} aria-hidden="true" />
        Mark done
      </button>
    </div>
  )
}

function ScheduledRow({
  block,
  onMutated,
}: {
  block: ScheduledBlock
  onMutated: () => void
}) {
  return (
    <li className="today-block">
      <div className="today-block-time" aria-hidden="true">
        <strong>{block.start_time}</strong>
        <span>{block.end_time}</span>
      </div>
      <div className="today-block-body">
        <div className="today-block-head">
          <Link to={`/tasks/${block.task_id}`} className="today-block-title">
            {block.title}
          </Link>
          <PriorityPill priority={block.priority} />
          <WorkflowPill status={block.workflow_status} />
          <DueSignalPill signal={block.due_signal} />
        </div>
        <div className="today-block-meta">
          <EstimateLabel minutes={block.estimated_minutes} assumed={block.estimate_assumed} />
          {block.due_date && (
            <span className="today-due-date">Due {formatDueDate(block.due_date)}</span>
          )}
          <span className="today-reason">{block.reason}</span>
        </div>
      </div>
      <TodayRowActions
        taskId={block.task_id}
        title={block.title}
        workflowStatus={block.workflow_status}
        onMutated={onMutated}
      />
    </li>
  )
}

function OverflowRow({
  task,
  onMutated,
}: {
  task: OverflowTask
  onMutated: () => void
}) {
  return (
    <li className="today-overflow-row">
      <Link to={`/tasks/${task.task_id}`} className="today-block-title">
        {task.title}
      </Link>
      <PriorityPill priority={task.priority} />
      <DueSignalPill signal={task.due_signal} />
      <EstimateLabel minutes={task.estimated_minutes} assumed={task.estimate_assumed} />
      <TodayRowActions
        taskId={task.task_id}
        title={task.title}
        workflowStatus={task.workflow_status}
        onMutated={onMutated}
      />
    </li>
  )
}

function BlockedRow({ task }: { task: BlockedTask }) {
  const count = task.blocking_tasks.length
  return (
    <li className="today-blocked-row">
      <div className="today-blocked-head">
        <Link to={`/tasks/${task.task_id}`} className="today-block-title">
          {task.title}
        </Link>
        <PriorityPill priority={task.priority} />
      </div>
      <span className="today-blocked-warning">
        <AlertTriangle size={13} aria-hidden="true" />
        Waiting on {count} unfinished {count === 1 ? 'dependency' : 'dependencies'}:
      </span>
      <ul className="today-blocker-list">
        {task.blocking_tasks.map((blocker) => (
          <li key={blocker.task_id} className="today-blocker">
            <Link to={`/tasks/${blocker.task_id}`} className="today-blocker-title">
              {blocker.title}
            </Link>
            <WorkflowPill status={blocker.workflow_status} />
          </li>
        ))}
      </ul>
    </li>
  )
}

export function TodayPage() {
  const {
    plan,
    loading,
    error,
    date,
    startTime,
    availableMinutes,
    setDate,
    setStartTime,
    setAvailableMinutes,
    refetch,
  } = useTodayPlan()

  const capacityOptions = CAPACITY_PRESETS.includes(availableMinutes)
    ? CAPACITY_PRESETS
    : [...CAPACITY_PRESETS, availableMinutes].sort((a, b) => a - b)

  return (
    <main className="today-page">
      <div className="section-heading">
        <div className="section-title">
          <span className="heading-icon tone-blue">
            <CalendarClock size={20} aria-hidden="true" />
          </span>
          <div>
            <h1>Today</h1>
            <p>A deterministic plan built from your accepted, open work.</p>
          </div>
        </div>
      </div>

      <div className="today-controls" role="group" aria-label="Plan controls">
        <label>
          <span>Day</span>
          <input
            type="date"
            value={date}
            onChange={(event) => setDate(event.target.value)}
          />
        </label>
        <label>
          <span>Start time</span>
          <input
            type="time"
            value={startTime}
            onChange={(event) => setStartTime(event.target.value || DEFAULT_START_TIME)}
          />
        </label>
        <label>
          <span>Capacity</span>
          <select
            value={availableMinutes}
            onChange={(event) =>
              setAvailableMinutes(Number(event.target.value) || DEFAULT_AVAILABLE_MINUTES)
            }
          >
            {capacityOptions.map((minutes) => (
              <option key={minutes} value={minutes}>
                {formatDuration(minutes)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading && <div className="page-loading">Loading today’s plan…</div>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {!loading && !error && plan && (
        <>
          <p className="today-summary">
            <strong>{formatDuration(plan.used_minutes)}</strong> planned of{' '}
            {formatDuration(plan.available_minutes)} capacity ·{' '}
            {plan.scheduled.length} scheduled · {plan.overflow.length} overflow ·{' '}
            {plan.blocked.length} blocked
          </p>

          {plan.scheduled.length > 0 ? (
            <section className="panel today-section" aria-labelledby="today-timeline-heading">
              <h2 id="today-timeline-heading">Timeline</h2>
              <ol className="today-timeline">
                {plan.scheduled.map((block) => (
                  <ScheduledRow key={block.task_id} block={block} onMutated={refetch} />
                ))}
              </ol>
            </section>
          ) : (
            <div className="empty-state today-empty">
              <Inbox size={20} aria-hidden="true" />
              {plan.overflow.length > 0 ? (
                <span>Nothing fit today’s capacity — see ranked work below.</span>
              ) : plan.blocked.length > 0 ? (
                <span>
                  Nothing schedulable — every open task is blocked by an unfinished
                  dependency.
                </span>
              ) : (
                <span>No open tasks to schedule for this day.</span>
              )}
            </div>
          )}

          {plan.overflow.length > 0 && (
            <section className="panel today-section" aria-labelledby="today-overflow-heading">
              <h2 id="today-overflow-heading">Didn’t fit ({plan.overflow.length})</h2>
              <p className="today-section-hint">
                Ranked unscheduled work — increase capacity or push these to another day.
              </p>
              <ul className="today-overflow-list">
                {plan.overflow.map((task) => (
                  <OverflowRow key={task.task_id} task={task} onMutated={refetch} />
                ))}
              </ul>
            </section>
          )}

          {plan.blocked.length > 0 && (
            <section className="panel today-section" aria-labelledby="today-blocked-heading">
              <h2 id="today-blocked-heading">Blocked ({plan.blocked.length})</h2>
              <p className="today-section-hint">
                Kept out of the schedule until their dependencies are done.
              </p>
              <ul className="today-blocked-list">
                {plan.blocked.map((task) => (
                  <BlockedRow key={task.task_id} task={task} />
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </main>
  )
}
