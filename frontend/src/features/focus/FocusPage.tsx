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
  Pin,
  PinOff,
  Play,
  Plus,
  SkipForward,
  X,
} from 'lucide-react'
import {
  addFocusCommitment,
  pinFocusTask,
  removeFocusEntry,
  updateFocusEntry,
} from '../../api/focus'
import { markTaskDone, skipOccurrence, updateTask } from '../../api/tasks'
import { useMobileViewport } from '../../hooks/useMobileViewport'
import { useToast } from '../../components/ToastContext'
import { fireAndForget } from '../../utils/async'
import type { TaskPriority, TaskWorkflowStatus } from '../../types/task'
import { SkipOccurrenceConfirm } from '../tasks/SkipOccurrenceConfirm'
import type {
  BlockedTask,
  CommitmentBlock,
  DueSignal,
  OverflowTask,
  ScheduledBlock,
} from '../../types/focus'
import { formatDuration } from '../../utils/duration'
import { addDaysISO, formatDueDate } from '../../utils/dates'
import { TaskPanelProvider } from '../tasks/panel/TaskPanelProvider'
import { useTaskLinkTo } from '../tasks/panel/taskPanelContext'
import { MobileFocus } from './mobile/MobileFocus'
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

/** Minutes from the plan date's midnight, so blocks on either side of it sort. */
function absoluteMinute(time: string, dayOffset: number): number {
  return dayOffset * 24 * 60 + parseTime(time)
}

// The timeline interleaves task blocks with fixed-time commitments (#319); the
// plan keeps them in separate lists so every scheduled block is still a task.
type TimelineItem =
  | { kind: 'task'; block: ScheduledBlock }
  | { kind: 'commitment'; commitment: CommitmentBlock }

function timelineItems(
  scheduled: ScheduledBlock[],
  commitments: CommitmentBlock[],
): TimelineItem[] {
  const items: TimelineItem[] = [
    ...scheduled.map((block) => ({ kind: 'task' as const, block })),
    ...commitments.map((commitment) => ({ kind: 'commitment' as const, commitment })),
  ]
  const start = (item: TimelineItem): number =>
    item.kind === 'task'
      ? absoluteMinute(item.block.start_time, item.block.start_day_offset)
      : absoluteMinute(item.commitment.start_time, item.commitment.start_day_offset)
  return items.sort((a, b) => start(a) - start(b))
}

function itemEnd(item: TimelineItem): number {
  return item.kind === 'task'
    ? absoluteMinute(item.block.end_time, item.block.end_day_offset)
    : absoluteMinute(item.commitment.end_time, item.commitment.end_day_offset)
}

function itemKey(item: TimelineItem): string {
  return item.kind === 'task'
    ? `task-${item.block.task_id}`
    : `commitment-${item.commitment.entry_id}`
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
//
// The stored day plan (#319) adds the pin verbs: an overflow row offers
// Schedule (a floating pin — only this task enters the day), a scheduled row
// offers Pin (optionally at a time, which makes it a fixed block) or Unpin.
function FocusRowActions({
  taskId,
  title,
  workflowStatus,
  isRecurring,
  planDate,
  onMutated,
  onSkip,
  pin = null,
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
  // Which pin verb the row offers: Schedule from "Didn't fit", Pin/Unpin on the
  // timeline. Null offers neither.
  pin?:
    | { kind: 'schedule' }
    | { kind: 'timeline'; entryId: number | null; startTime: string }
    | null
}) {
  const { withToast } = useToast()
  const [pending, setPending] = useState(false)
  // The inline "Pin at" time field, open while choosing a time.
  const [pinAt, setPinAt] = useState<string | null>(null)

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
      {pin?.kind === 'schedule' && (
        <button
          type="button"
          className="task-action"
          disabled={pending}
          aria-label={`Schedule ${title} today`}
          onClick={() =>
            void run(() => pinFocusTask(planDate, taskId), 'Scheduled into the day')
          }
        >
          <Pin size={15} aria-hidden="true" />
          Schedule
        </button>
      )}
      {pin?.kind === 'timeline' && pin.entryId !== null && (
        <button
          type="button"
          className="task-action"
          disabled={pending}
          aria-label={`Unpin ${title}`}
          onClick={() => {
            const entryId = pin.entryId
            if (entryId !== null) void run(() => removeFocusEntry(entryId), 'Unpinned')
          }}
        >
          <PinOff size={15} aria-hidden="true" />
          Unpin
        </button>
      )}
      {pin?.kind === 'timeline' && pin.entryId === null && pinAt === null && (
        <button
          type="button"
          className="task-action"
          disabled={pending}
          aria-label={`Pin ${title}`}
          onClick={() => setPinAt(pin.startTime)}
        >
          <Pin size={15} aria-hidden="true" />
          Pin
        </button>
      )}
      {pin?.kind === 'timeline' && pinAt !== null && (
        <form
          className="focus-pin-form"
          onSubmit={(event) => {
            event.preventDefault()
            void run(
              () => pinFocusTask(planDate, taskId, pinAt || undefined),
              pinAt ? `Pinned at ${pinAt}` : 'Pinned into the day',
            )
          }}
        >
          <input
            type="time"
            aria-label={`Pin ${title} at`}
            value={pinAt}
            onChange={(event) => setPinAt(event.target.value)}
          />
          <button type="submit" className="task-action" disabled={pending}>
            Pin
          </button>
          <button
            type="button"
            className="task-action"
            aria-label="Cancel pin"
            onClick={() => setPinAt(null)}
          >
            <X size={15} aria-hidden="true" />
          </button>
        </form>
      )}
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

/** The pinned/fixed state as a colored meta word (never a pill). */
function PinWord({ block }: { block: ScheduledBlock }) {
  if (!block.pinned) return null
  return block.fixed ? (
    <span className="focus-pin-word">pinned at {block.start_time}</span>
  ) : (
    <span className="focus-pin-word">pinned</span>
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
          <PinWord block={block} />
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
        pin={{ kind: 'timeline', entryId: block.entry_id, startTime: block.start_time }}
      />
    </li>
  )
}

// A fixed-time commitment that is not a task (a meeting): no task link, no
// task verbs — only Move (to another time) and Remove.
function CommitmentRow({
  commitment,
  past,
  planDate,
  onMutated,
}: {
  commitment: CommitmentBlock
  past: boolean
  planDate: string
  onMutated: () => void
}) {
  const { withToast } = useToast()
  const [pending, setPending] = useState(false)
  const [moveTo, setMoveTo] = useState<string | null>(null)

  async function run(action: () => Promise<unknown>, success: string): Promise<void> {
    if (pending) return
    setPending(true)
    try {
      await withToast(action(), { success })
      setMoveTo(null)
      onMutated()
    } catch {
      // The error toast already surfaced; keep the row interactive to retry.
    } finally {
      setPending(false)
    }
  }

  return (
    <li
      className={
        past ? 'focus-block focus-commitment focus-block-past' : 'focus-block focus-commitment'
      }
    >
      <div className="focus-block-time" aria-hidden="true">
        <strong>
          {formatBlockTime(planDate, commitment.start_time, commitment.start_day_offset)}
        </strong>
        <span>{formatBlockTime(planDate, commitment.end_time, commitment.end_day_offset)}</span>
      </div>
      <div className="focus-block-body">
        <div className="focus-block-head">
          <span className="focus-block-title">{commitment.title}</span>
        </div>
        <div className="focus-block-meta">
          <span className="focus-commitment-word">fixed</span>
          <EstimateLabel minutes={commitment.duration_minutes} assumed={false} />
        </div>
      </div>
      <div className="focus-row-actions">
        {moveTo === null ? (
          <button
            type="button"
            className="task-action"
            disabled={pending}
            aria-label={`Move ${commitment.title}`}
            onClick={() => setMoveTo(commitment.start_time)}
          >
            <Clock3 size={15} aria-hidden="true" />
            Move
          </button>
        ) : (
          <form
            className="focus-pin-form"
            onSubmit={(event) => {
              event.preventDefault()
              if (!moveTo) return
              void run(
                () => updateFocusEntry(commitment.entry_id, { start_time: moveTo }),
                `Moved to ${moveTo}`,
              )
            }}
          >
            <input
              type="time"
              required
              aria-label={`Move ${commitment.title} to`}
              value={moveTo}
              onChange={(event) => setMoveTo(event.target.value)}
            />
            <button type="submit" className="task-action" disabled={pending}>
              Move
            </button>
            <button
              type="button"
              className="task-action"
              aria-label="Cancel move"
              onClick={() => setMoveTo(null)}
            >
              <X size={15} aria-hidden="true" />
            </button>
          </form>
        )}
        <button
          type="button"
          className="task-action"
          disabled={pending}
          aria-label={`Remove ${commitment.title}`}
          onClick={() =>
            void run(() => removeFocusEntry(commitment.entry_id), 'Commitment removed')
          }
        >
          <X size={15} aria-hidden="true" />
          Remove
        </button>
      </div>
    </li>
  )
}

// Commitment lengths offered by the add form; 30 minutes is the default.
const COMMITMENT_MINUTES = [15, 30, 45, 60, 90, 120]

/**
 * Block fixed time for something that is not a task. Lives on the Focus page,
 * the lane it belongs to — there is no global way to create one.
 */
function AddCommitmentForm({
  planDate,
  onMutated,
}: {
  planDate: string
  onMutated: () => void
}) {
  const { withToast } = useToast()
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [start, setStart] = useState('')
  const [minutes, setMinutes] = useState(30)
  const [pending, setPending] = useState(false)

  if (!open) {
    return (
      <button type="button" className="task-action focus-add-commitment" onClick={() => setOpen(true)}>
        <Plus size={15} aria-hidden="true" />
        Add commitment
      </button>
    )
  }

  async function submit(): Promise<void> {
    if (pending || !title.trim() || !start) return
    setPending(true)
    try {
      await withToast(addFocusCommitment(planDate, title.trim(), start, minutes), {
        success: `Added ${title.trim()} at ${start}`,
      })
      setTitle('')
      setStart('')
      setOpen(false)
      onMutated()
    } catch {
      // The error toast already surfaced; keep the form to retry.
    } finally {
      setPending(false)
    }
  }

  return (
    <form
      className="focus-commitment-form"
      aria-label="Add commitment"
      onSubmit={(event) => {
        event.preventDefault()
        void submit()
      }}
    >
      <input
        type="text"
        aria-label="Commitment title"
        placeholder="Standup"
        value={title}
        maxLength={200}
        required
        onChange={(event) => setTitle(event.target.value)}
      />
      <input
        type="time"
        aria-label="Commitment start"
        value={start}
        required
        onChange={(event) => setStart(event.target.value)}
      />
      <select
        aria-label="Commitment length"
        value={minutes}
        onChange={(event) => setMinutes(Number(event.target.value))}
      >
        {COMMITMENT_MINUTES.map((value) => (
          <option key={value} value={value}>
            {formatDuration(value)}
          </option>
        ))}
      </select>
      <button type="submit" className="task-action" disabled={pending}>
        Add
      </button>
      <button type="button" className="task-action" onClick={() => setOpen(false)}>
        Cancel
      </button>
    </form>
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
        pin={{ kind: 'schedule' }}
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
    setConsumedMinutes,
    refetch,
  } = useFocusPlan()

  // Below the phone breakpoint the route swaps to the M05i timeline: one object
  // per block, duration as height, swipe to act. Desktop keeps its controls and
  // its four-column rows.
  const mobile = useMobileViewport()
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
  const timeline = plan ? timelineItems(plan.scheduled, plan.commitments) : []
  let nowIndex: number | null = null
  if (viewingToday && plan) {
    const upcoming = timeline.findIndex((item) => itemEnd(item) > nowMinutes)
    nowIndex = upcoming === -1 ? timeline.length : upcoming
  }
  const nowLabel = `${String(Math.floor(nowMinutes / 60)).padStart(2, '0')}:${String(
    nowMinutes % 60,
  ).padStart(2, '0')}`

  // Returned early rather than branched inside the tree: the two surfaces share
  // the plan, the skip confirm and the peek panel, and nothing else.
  if (mobile) {
    return (
      <TaskPanelProvider onMutated={refetch}>
        <main className="focus-page focus-page-mobile">
          <MobileFocus
            plan={plan}
            loading={loading}
            error={error}
            date={date}
            today={localToday()}
            startTime={startTime}
            capacity={availableMinutes}
            setDate={setDate}
            setStartTime={setStartTime}
            setCapacityMinutes={setCapacityMinutes}
            setConsumedMinutes={setConsumedMinutes}
            refetch={refetch}
            onSkip={(id, title) => setSkipTarget({ id, title })}
          />
          <SkipOccurrenceConfirm
            taskTitle={skipTarget?.title ?? null}
            onCancel={() => setSkipTarget(null)}
            onConfirm={() => fireAndForget(confirmSkip())}
          />
        </main>
      </TaskPanelProvider>
    )
  }

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

          {timeline.length > 0 ? (
            <section className="panel focus-section" aria-labelledby="focus-timeline-heading">
              <h2 id="focus-timeline-heading">Timeline</h2>
              <ol className="focus-timeline">
                {timeline.map((item, index) => (
                  <Fragment key={itemKey(item)}>
                    {index === nowIndex && <NowMarker label={nowLabel} />}
                    {item.kind === 'task' ? (
                      <ScheduledRow
                        block={item.block}
                        past={nowIndex !== null && index < nowIndex}
                        planDate={plan.date}
                        onMutated={refetch}
                        onSkip={(id, title) => setSkipTarget({ id, title })}
                      />
                    ) : (
                      <CommitmentRow
                        commitment={item.commitment}
                        past={nowIndex !== null && index < nowIndex}
                        planDate={plan.date}
                        onMutated={refetch}
                      />
                    )}
                  </Fragment>
                ))}
                {nowIndex === timeline.length && <NowMarker label={nowLabel} />}
              </ol>
              <AddCommitmentForm planDate={plan.date} onMutated={refetch} />
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
              <AddCommitmentForm planDate={plan.date} onMutated={refetch} />
            </div>
          )}

          {plan.overflow.length > 0 && (
            <CollapsibleSection
              id="focus-overflow-heading"
              title="Didn’t fit"
              count={plan.overflow.length}
              hint="Ranked unscheduled work — schedule one into the day, increase capacity, or push these to another day."
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
