import { Fragment, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronsRight,
  Clock3,
  Inbox,
  Play,
  SkipForward,
} from 'lucide-react'
import { markTaskDone, skipOccurrence, updateTask } from '../../api/tasks'
import { useToast } from '../../components/ToastContext'
import { fireAndForget } from '../../utils/async'
import type { TaskPriority, TaskWorkflowStatus } from '../../types/task'
import { SkipOccurrenceConfirm } from '../tasks/SkipOccurrenceConfirm'
import type {
  BlockedTask,
  DueSignal,
  OverflowTask,
  ScheduledBlock,
} from '../../types/focus'
import { formatDuration } from '../../utils/duration'
import { addDaysISO, formatDueDate } from '../../utils/dates'
import { TaskPanelProvider } from '../tasks/panel/TaskPanelProvider'
import { useTaskLinkTo } from '../tasks/panel/taskPanelContext'
import { DEFAULT_START_TIME, useFocusPlan } from './useFocusPlan'

// Capacity presets keep the control daily-scannable while staying inside the
// backend's 15–1440 bound. The current value is added if it isn't a preset so a
// deep-linked or odd value still renders. 'until_end' switches to computing
// capacity from the start time until the chosen end-of-day time.
const CAPACITY_PRESETS = [30, 60, 120, 240, 360, 480]
const UNTIL_END_VALUE = 'until_end'

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

/** "YYYY-MM-DD" -> the next calendar day, for deferring out of this plan. */
function nextDay(date: string): string {
  const [year, month, day] = date.split('-').map(Number)
  const next = new Date(year, month - 1, day + 1)
  const m = String(next.getMonth() + 1).padStart(2, '0')
  const d = String(next.getDate()).padStart(2, '0')
  return `${next.getFullYear()}-${m}-${d}`
}

function localToday(): string {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

/** Current local time as minutes from midnight, refreshed every minute. */
function useNowMinutes(): number {
  const [now, setNow] = useState(() => {
    const d = new Date()
    return d.getHours() * 60 + d.getMinutes()
  })
  useEffect(() => {
    const timer = setInterval(() => {
      const d = new Date()
      setNow(d.getHours() * 60 + d.getMinutes())
    }, 60_000)
    return () => clearInterval(timer)
  }, [])
  return now
}

function parseTime(value: string): number {
  const [hours, minutes] = value.split(':')
  return Number(hours) * 60 + Number(minutes)
}

function formatBlockTime(planDate: string, time: string, dayOffset: number): string {
  if (dayOffset === 0) return time
  return `${formatDueDate(addDaysISO(planDate, dayOffset))} · ${time}`
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
    <span className="focus-estimate">
      <Clock3 size={13} aria-hidden="true" />
      {formatDuration(minutes)}
      {assumed && (
        <span className="focus-assumed" title="No estimate on the task; assumed default">
          assumed
        </span>
      )}
    </span>
  )
}

// In-row Start / Mark done / Defer actions. Reuses the existing task endpoints
// and asks the parent to refetch on success so the row re-ranks (Start) or
// drops out (done, defer). Mark done MUST go through the dedicated done
// endpoint so recurrence's next-occurrence creation still fires — never a raw
// PATCH workflow_status=done. Defer snoozes the task to the day after the
// plan's date via `deferred_until`; the scheduler skips it until then.
function FocusRowActions({
  taskId,
  title,
  workflowStatus,
  isRecurring,
  planDate,
  onMutated,
  onSkip,
}: {
  taskId: number
  title: string
  workflowStatus: TaskWorkflowStatus
  isRecurring: boolean
  planDate: string
  onMutated: () => void
  // Opens the page-level skip confirm; the actual skip + refetch run there so a
  // row unmounting mid-confirm doesn't strand the dialog.
  onSkip: (taskId: number, title: string) => void
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
    <div className="focus-row-actions">
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
      <button
        type="button"
        className="task-action"
        disabled={pending}
        aria-label={`Defer ${title} to tomorrow`}
        onClick={() =>
          void run(
            () => updateTask(taskId, { deferred_until: nextDay(planDate) }),
            'Deferred to tomorrow',
          )
        }
      >
        <ChevronsRight size={15} aria-hidden="true" />
        Defer
      </button>
      {isRecurring && (
        <button
          type="button"
          className="task-action"
          disabled={pending}
          aria-label={`Skip this occurrence of ${title}`}
          onClick={() => onSkip(taskId, title)}
        >
          <SkipForward size={15} aria-hidden="true" />
          Skip
        </button>
      )}
    </div>
  )
}

// The "now" divider. Sits between rows, or after the last row once the whole
// day's schedule has elapsed.
function NowMarker({ label }: { label: string }) {
  return (
    <li className="focus-now-marker" aria-label={`Now, ${label}`}>
      <span>Now · {label}</span>
    </li>
  )
}

function ScheduledRow({
  block,
  past,
  planDate,
  onMutated,
  onSkip,
}: {
  block: ScheduledBlock
  past: boolean
  planDate: string
  onMutated: () => void
  onSkip: (taskId: number, title: string) => void
}) {
  const taskLinkTo = useTaskLinkTo()
  return (
    <li className={past ? 'focus-block focus-block-past' : 'focus-block'}>
      <div className="focus-block-time" aria-hidden="true">
        <strong>{formatBlockTime(planDate, block.start_time, block.start_day_offset)}</strong>
        <span>{formatBlockTime(planDate, block.end_time, block.end_day_offset)}</span>
      </div>
      <div className="focus-block-body">
        <div className="focus-block-head">
          <Link to={taskLinkTo(block.task_id)} className="focus-block-title">
            {block.title}
          </Link>
          <PriorityPill priority={block.priority} />
          <WorkflowPill status={block.workflow_status} />
          <DueSignalPill signal={block.due_signal} />
        </div>
        <div className="focus-block-meta">
          <EstimateLabel minutes={block.estimated_minutes} assumed={block.estimate_assumed} />
          {block.due_date && (
            <span className="focus-due-date">Due {formatDueDate(block.due_date)}</span>
          )}
          <span className="focus-reason">{block.reason}</span>
        </div>
      </div>
      <FocusRowActions
        taskId={block.task_id}
        title={block.title}
        workflowStatus={block.workflow_status}
        isRecurring={block.is_recurring}
        planDate={planDate}
        onMutated={onMutated}
        onSkip={onSkip}
      />
    </li>
  )
}

function OverflowRow({
  task,
  planDate,
  onMutated,
  onSkip,
}: {
  task: OverflowTask
  planDate: string
  onMutated: () => void
  onSkip: (taskId: number, title: string) => void
}) {
  const taskLinkTo = useTaskLinkTo()
  return (
    <li className="focus-overflow-row">
      <Link to={taskLinkTo(task.task_id)} className="focus-block-title">
        {task.title}
      </Link>
      <PriorityPill priority={task.priority} />
      <DueSignalPill signal={task.due_signal} />
      <EstimateLabel minutes={task.estimated_minutes} assumed={task.estimate_assumed} />
      {task.scheduled_subtask_count > 0 && (
        <span className="focus-partial" title="Part of this task is on the timeline">
          {task.scheduled_subtask_count}{' '}
          {task.scheduled_subtask_count === 1 ? 'subtask' : 'subtasks'} scheduled
        </span>
      )}
      <FocusRowActions
        taskId={task.task_id}
        title={task.title}
        workflowStatus={task.workflow_status}
        isRecurring={task.is_recurring}
        planDate={planDate}
        onMutated={onMutated}
        onSkip={onSkip}
      />
    </li>
  )
}

function BlockedRow({ task }: { task: BlockedTask }) {
  const taskLinkTo = useTaskLinkTo()
  const count = task.blocking_tasks.length
  return (
    <li className="focus-blocked-row">
      <div className="focus-blocked-head">
        <Link to={taskLinkTo(task.task_id)} className="focus-block-title">
          {task.title}
        </Link>
        <PriorityPill priority={task.priority} />
      </div>
      <span className="focus-blocked-warning">
        <AlertTriangle size={13} aria-hidden="true" />
        Waiting on {count} unfinished {count === 1 ? 'dependency' : 'dependencies'}:
      </span>
      <ul className="focus-blocker-list">
        {task.blocking_tasks.map((blocker) => (
          <li key={blocker.task_id} className="focus-blocker">
            <Link to={taskLinkTo(blocker.task_id)} className="focus-blocker-title">
              {blocker.title}
            </Link>
            <WorkflowPill status={blocker.workflow_status} />
          </li>
        ))}
      </ul>
    </li>
  )
}

// Secondary sections ("Didn't fit", "Blocked") start collapsed so the timeline
// owns the page; the header row still shows the count at a glance.
function CollapsibleSection({
  id,
  title,
  count,
  hint,
  children,
}: {
  id: string
  title: string
  count: number
  hint: string
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  return (
    <section className="panel focus-section focus-collapsible" aria-labelledby={id}>
      <button
        type="button"
        className="focus-collapse-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? (
          <ChevronDown size={16} aria-hidden="true" />
        ) : (
          <ChevronRight size={16} aria-hidden="true" />
        )}
        <h2 id={id}>
          {title} ({count})
        </h2>
      </button>
      {open && (
        <>
          <p className="focus-section-hint">{hint}</p>
          {children}
        </>
      )}
    </section>
  )
}

export function FocusPage() {
  const {
    plan,
    loading,
    error,
    windowError,
    date,
    startTime,
    availableMinutes,
    capacityMode,
    capacityMinutes,
    endOfDay,
    setDate,
    setStartTime,
    setCapacityMinutes,
    setCapacityMode,
    setEndOfDay,
    refetch,
  } = useFocusPlan()

  const nowMinutes = useNowMinutes()
  const viewingToday = date === localToday()
  const { withToast } = useToast()
  // The recurring task whose skip is awaiting confirmation (null = no dialog).
  const [skipTarget, setSkipTarget] = useState<{ id: number; title: string } | null>(
    null,
  )

  async function confirmSkip() {
    if (!skipTarget) return
    const { id } = skipTarget
    setSkipTarget(null)
    await withToast(skipOccurrence(id), { success: 'Occurrence skipped' })
    refetch()
  }

  const capacityOptions =
    capacityMode === 'minutes' && !CAPACITY_PRESETS.includes(capacityMinutes)
      ? [...CAPACITY_PRESETS, capacityMinutes].sort((a, b) => a - b)
      : CAPACITY_PRESETS

  // Insertion index of the "now" divider: the first block still in the future,
  // or `scheduled.length` when the whole day has elapsed (marker lands after the
  // final row and every block is dimmed). `null` means "not viewing today", so
  // no marker and no dimming — distinct from the fully-elapsed case, which a
  // bare findIndex() of -1 used to conflate.
  let nowIndex: number | null = null
  if (viewingToday && plan) {
    const upcoming = plan.scheduled.findIndex(
      (block) => block.end_day_offset * 24 * 60 + parseTime(block.end_time) > nowMinutes,
    )
    nowIndex = upcoming === -1 ? plan.scheduled.length : upcoming
  }
  const nowLabel = `${String(Math.floor(nowMinutes / 60)).padStart(2, '0')}:${String(
    nowMinutes % 60,
  ).padStart(2, '0')}`

  return (
    <TaskPanelProvider onMutated={refetch}>
    <main className="focus-page">
      <div className="section-heading">
        {/* No icon tile — the page title carries the page. */}
        <div className="section-title">
          <div>
            <h1>Focus</h1>
            <p>Choose a session window and start with your highest-ranked work.</p>
          </div>
        </div>
      </div>

      <div className="focus-controls" role="group" aria-label="Plan controls">
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
            value={capacityMode === 'until_end' ? UNTIL_END_VALUE : capacityMinutes}
            onChange={(event) => {
              if (event.target.value === UNTIL_END_VALUE) {
                setCapacityMode('until_end')
              } else {
                setCapacityMinutes(Number(event.target.value))
              }
            }}
          >
            {capacityOptions.map((minutes) => (
              <option key={minutes} value={minutes}>
                {formatDuration(minutes)}
              </option>
            ))}
            <option value={UNTIL_END_VALUE}>Until end of day</option>
          </select>
        </label>
        {capacityMode === 'until_end' && (
          <label>
            <span>End of day</span>
            <input
              type="time"
              aria-label="End of day"
              value={endOfDay}
              aria-invalid={windowError ? 'true' : undefined}
              aria-describedby={windowError ? 'focus-window-error' : undefined}
              onChange={(event) => setEndOfDay(event.target.value)}
            />
            {windowError && (
              <span id="focus-window-error" role="alert" className="focus-control-error">
                {windowError}
              </span>
            )}
          </label>
        )}
      </div>

      {loading && !windowError && (
        <div className="page-loading">Preparing your focus session…</div>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {!loading && !error && !windowError && plan && (
        <>
          <p className="focus-summary">
            <strong>{formatDuration(plan.used_minutes)}</strong> planned of{' '}
            {formatDuration(availableMinutes)} capacity ·{' '}
            {plan.scheduled.length} scheduled · {plan.overflow.length} overflow ·{' '}
            {plan.blocked.length} blocked
          </p>

          {plan.scheduled.length > 0 ? (
            <section className="panel focus-section" aria-labelledby="focus-timeline-heading">
              <h2 id="focus-timeline-heading">Timeline</h2>
              <ol className="focus-timeline">
                {plan.scheduled.map((block, index) => (
                  <Fragment key={block.task_id}>
                    {index === nowIndex && <NowMarker label={nowLabel} />}
                    <ScheduledRow
                      block={block}
                      past={nowIndex !== null && index < nowIndex}
                      planDate={plan.date}
                      onMutated={refetch}
                      onSkip={(id, title) => setSkipTarget({ id, title })}
                    />
                  </Fragment>
                ))}
                {nowIndex === plan.scheduled.length && <NowMarker label={nowLabel} />}
              </ol>
            </section>
          ) : (
            <div className="empty-state focus-empty">
              <Inbox size={20} aria-hidden="true" />
              {plan.overflow.length > 0 ? (
                <span>Nothing fit this session’s capacity — see ranked work below.</span>
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
            <CollapsibleSection
              id="focus-overflow-heading"
              title="Didn’t fit"
              count={plan.overflow.length}
              hint="Ranked unscheduled work — increase capacity or push these to another day."
            >
              <ul className="focus-overflow-list">
                {plan.overflow.map((task) => (
                  <OverflowRow
                    key={task.task_id}
                    task={task}
                    planDate={plan.date}
                    onMutated={refetch}
                    onSkip={(id, title) => setSkipTarget({ id, title })}
                  />
                ))}
              </ul>
            </CollapsibleSection>
          )}

          {plan.blocked.length > 0 && (
            <CollapsibleSection
              id="focus-blocked-heading"
              title="Blocked"
              count={plan.blocked.length}
              hint="Kept out of the schedule until their dependencies are done."
            >
              <ul className="focus-blocked-list">
                {plan.blocked.map((task) => (
                  <BlockedRow key={task.task_id} task={task} />
                ))}
              </ul>
            </CollapsibleSection>
          )}
        </>
      )}

      <SkipOccurrenceConfirm
        taskTitle={skipTarget?.title ?? null}
        onCancel={() => setSkipTarget(null)}
        onConfirm={() => fireAndForget(confirmSkip())}
      />
    </main>
    </TaskPanelProvider>
  )
}
