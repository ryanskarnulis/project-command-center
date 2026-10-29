import { Fragment, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  CalendarX,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronsRight,
  Clock3,
  GripVertical,
  Inbox,
  Pin,
  PinOff,
  Play,
  Plus,
  SkipForward,
  Undo2,
  X,
} from 'lucide-react'
import {
  addFocusCommitment,
  editFocusOccurrence,
  pinFocusTask,
  removeFocusEntry,
  reorderFocusPins,
  restoreFocusEntry,
  skipFocusOccurrence,
  stopFocusSeries,
  updateFocusEntry,
} from '../../api/focus'
import { markTaskDone, skipOccurrence, updateTask } from '../../api/tasks'
import { useMobileViewport } from '../../hooks/useMobileViewport'
import { useToast, type ToastAction } from '../../components/ToastContext'
import { fireAndForget } from '../../utils/async'
import type { TaskPriority, TaskWorkflowStatus } from '../../types/task'
import { SkipOccurrenceConfirm } from '../tasks/SkipOccurrenceConfirm'
import type {
  BlockedTask,
  CommitmentBlock,
  DueSignal,
  OverflowTask,
  ScheduledBlock,
  SkippedCommitmentBlock,
} from '../../types/focus'
import { formatDuration } from '../../utils/duration'
import {
  COMMITMENT_REPEATS,
  commitmentRepeatInterval,
  formatCommitmentRepeat,
  type CommitmentRepeat,
} from './commitmentRepeat'
import { addDaysISO, formatDueDate } from '../../utils/dates'
import { TaskPanelProvider } from '../tasks/panel/TaskPanelProvider'
import { useTaskLinkTo } from '../tasks/panel/taskPanelContext'
import { MobileFocus } from './mobile/MobileFocus'
import { dropPin, floatingPinOrder, shiftPin } from './pinOrder'
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
// A skipped occurrence (#429) sits at its time too, muted, so it can be taken
// back; it holds no time.
type TimelineItem =
  | { kind: 'task'; block: ScheduledBlock }
  | { kind: 'commitment'; commitment: CommitmentBlock }
  | { kind: 'skipped'; commitment: SkippedCommitmentBlock }

function timelineItems(
  scheduled: ScheduledBlock[],
  commitments: CommitmentBlock[],
  skipped: SkippedCommitmentBlock[],
): TimelineItem[] {
  const items: TimelineItem[] = [
    ...scheduled.map((block) => ({ kind: 'task' as const, block })),
    ...commitments.map((commitment) => ({ kind: 'commitment' as const, commitment })),
    ...skipped.map((commitment) => ({ kind: 'skipped' as const, commitment })),
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
  if (item.kind === 'task') return `task-${item.block.task_id}`
  if (item.kind === 'skipped') return `skipped-${item.commitment.skip_entry_id}`
  return `commitment-${item.commitment.entry_id}`
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
    // Engaged while its "Pin at" field is open, so the cluster stays shown on a
    // desktop row even once the pointer leaves it (see `.focus-row-actions`).
    <div className={pinAt !== null ? 'focus-row-actions focus-row-actions-engaged' : 'focus-row-actions'}>
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

// dataTransfer type for dragging a floating pin to a new place (#375).
const PIN_DRAG_TYPE = 'application/x-pcc-focus-pin'

/**
 * How a floating pin's row takes part in reordering (#375): the whole row
 * drags onto another floating pin's slot, and its grip handle moves it with
 * the arrow keys, so the reorder is never pointer-only.
 */
interface PinReorder {
  entryId: number
  dragging: boolean
  dropTarget: boolean
  /** Return focus to the grip once the reordered plan has rendered. */
  focusGrip: boolean
  onDragStart: (entryId: number) => void
  onDragOver: (entryId: number) => void
  onDrop: (entryId: number) => void
  onDragEnd: () => void
  onKeyMove: (entryId: number, delta: number) => void
}

function PinGrip({ title, reorder }: { title: string; reorder: PinReorder }) {
  const ref = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (reorder.focusGrip) ref.current?.focus()
  }, [reorder.focusGrip])
  return (
    <button
      ref={ref}
      type="button"
      className="focus-pin-grip"
      aria-label={`Reorder ${title}`}
      title="Drag to reorder, or use the up and down arrow keys"
      onKeyDown={(event) => {
        if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
        event.preventDefault()
        reorder.onKeyMove(reorder.entryId, event.key === 'ArrowUp' ? -1 : 1)
      }}
    >
      <GripVertical size={14} aria-hidden="true" />
    </button>
  )
}

function ScheduledRow({
  block,
  past,
  current,
  planDate,
  onMutated,
  onSkip,
  reorder = null,
}: {
  block: ScheduledBlock
  past: boolean
  // The block the "now" marker sits above: its actions stay shown on desktop.
  current: boolean
  planDate: string
  onMutated: () => void
  onSkip: (taskId: number, title: string) => void
  // Set only on a floating pin when the day has more than one of them.
  reorder?: PinReorder | null
}) {
  const taskLinkTo = useTaskLinkTo()
  const className = [
    'focus-block',
    past && 'focus-block-past',
    current && 'focus-block-current',
    reorder && 'focus-block-reorderable',
    reorder?.dragging && 'focus-block-dragging',
    reorder?.dropTarget && 'focus-block-drop-target',
  ]
    .filter(Boolean)
    .join(' ')
  const dragProps = reorder
    ? {
        draggable: true,
        onDragStart: (event: React.DragEvent) => {
          event.dataTransfer.setData(PIN_DRAG_TYPE, String(reorder.entryId))
          event.dataTransfer.effectAllowed = 'move'
          reorder.onDragStart(reorder.entryId)
        },
        onDragOver: (event: React.DragEvent) => {
          if (!event.dataTransfer.types.includes(PIN_DRAG_TYPE)) return
          event.preventDefault()
          event.dataTransfer.dropEffect = 'move'
          reorder.onDragOver(reorder.entryId)
        },
        onDrop: (event: React.DragEvent) => {
          if (!event.dataTransfer.types.includes(PIN_DRAG_TYPE)) return
          event.preventDefault()
          reorder.onDrop(reorder.entryId)
        },
        onDragEnd: () => reorder.onDragEnd(),
      }
    : {}
  return (
    <li className={className} data-entry-id={reorder?.entryId} {...dragProps}>
      <div className="focus-block-time">
        <strong aria-hidden="true">
          {formatBlockTime(planDate, block.start_time, block.start_day_offset)}
        </strong>
        <span aria-hidden="true">
          {formatBlockTime(planDate, block.end_time, block.end_day_offset)}
        </span>
        {reorder && <PinGrip title={block.title} reorder={reorder} />}
      </div>
      <div className="focus-block-body">
        <div className="focus-block-head">
          <Link to={taskLinkTo(block.task_id)} className="focus-block-title">
            {block.title}
          </Link>
          <PriorityPill priority={block.priority} />
          {/* Exceptions only: every scheduled task is open unless started, so
              "open" says nothing; "in progress" is the one worth a word. */}
          {block.workflow_status === 'in_progress' && (
            <WorkflowPill status={block.workflow_status} />
          )}
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
// task verbs — only Move (to another time) and Remove. A repeating one (#376)
// also offers Skip day; its Remove acts on the whole series. Since #428 a
// repeating one's Move asks "this day" or "every day", it can be stopped from
// this day on (the days before keep it), and a day edited on its own says so
// and offers Reset day.
function CommitmentRow({
  commitment,
  past,
  current,
  planDate,
  onMutated,
}: {
  commitment: CommitmentBlock
  past: boolean
  current: boolean
  planDate: string
  onMutated: () => void
}) {
  const { withToast } = useToast()
  const [pending, setPending] = useState(false)
  const [moveTo, setMoveTo] = useState<string | null>(null)
  const repeat = commitment.repeat_interval
  const overrideId = commitment.override_entry_id
  // Stopping on the first day would leave nothing: that is Remove series.
  const canStop =
    repeat !== null &&
    commitment.series_start_date !== null &&
    commitment.series_start_date < planDate
  const undoAction = useUndoAction(onMutated)

  async function run<T>(
    action: () => Promise<T>,
    success: string,
    undo?: (result: T) => Promise<unknown>,
  ): Promise<void> {
    if (pending) return
    setPending(true)
    try {
      await withToast(action(), {
        success,
        action: undo && ((result: T) => undoAction(() => undo(result))),
      })
      setMoveTo(null)
      onMutated()
    } catch {
      // The error toast already surfaced; keep the row interactive to retry.
    } finally {
      setPending(false)
    }
  }

  function moveThisDay(start: string): void {
    const previous = commitment.start_time
    void run(
      () => editFocusOccurrence(commitment.entry_id, planDate, { start_time: start }),
      `Moved to ${start} on this day only`,
      // A new edit is undone by removing it; an edit of an edit, by moving back.
      (edit) =>
        overrideId === null
          ? removeFocusEntry(edit.id)
          : editFocusOccurrence(commitment.entry_id, planDate, { start_time: previous }),
    )
  }

  function moveEveryDay(start: string): void {
    void run(
      () => updateFocusEntry(commitment.entry_id, { start_time: start }),
      repeat ? `Moved to ${start} every time` : `Moved to ${start}`,
    )
  }

  return (
    <li
      className={[
        'focus-block focus-commitment',
        past && 'focus-block-past',
        current && 'focus-block-current',
      ]
        .filter(Boolean)
        .join(' ')}
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
          {repeat && <span className="focus-repeat-word">{formatCommitmentRepeat(repeat)}</span>}
          {overrideId !== null && <span className="focus-edited-word">this day only</span>}
          <EstimateLabel minutes={commitment.duration_minutes} assumed={false} />
        </div>
      </div>
      <div
        className={moveTo !== null ? 'focus-row-actions focus-row-actions-engaged' : 'focus-row-actions'}
      >
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
              // Enter in the time field: a series moves this day only, the
              // narrower of its two choices.
              if (repeat) moveThisDay(moveTo)
              else moveEveryDay(moveTo)
            }}
          >
            <input
              type="time"
              required
              aria-label={`Move ${commitment.title} to`}
              value={moveTo}
              onChange={(event) => setMoveTo(event.target.value)}
            />
            {repeat ? (
              <>
                <button type="submit" className="task-action" disabled={pending}>
                  This day
                </button>
                <button
                  type="button"
                  className="task-action"
                  disabled={pending}
                  onClick={() => moveTo && moveEveryDay(moveTo)}
                >
                  Every day
                </button>
              </>
            ) : (
              <button type="submit" className="task-action" disabled={pending}>
                Move
              </button>
            )}
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
        {overrideId !== null && (
          <button
            type="button"
            className="task-action"
            disabled={pending}
            aria-label={`Reset ${commitment.title} on this day`}
            onClick={() =>
              void run(
                () => removeFocusEntry(overrideId),
                `${commitment.title} is back to its usual time on this day`,
                () => restoreFocusEntry(overrideId),
              )
            }
          >
            <Undo2 size={15} aria-hidden="true" />
            Reset day
          </button>
        )}
        {repeat && overrideId === null && (
          <button
            type="button"
            className="task-action"
            disabled={pending}
            aria-label={`Skip ${commitment.title} on this day`}
            onClick={() =>
              void run(
                () => skipFocusOccurrence(commitment.entry_id, planDate),
                `Skipped ${commitment.title} on this day`,
                // Undo removes the skip, which brings the occurrence back.
                (skip) => removeFocusEntry(skip.id),
              )
            }
          >
            <SkipForward size={15} aria-hidden="true" />
            Skip day
          </button>
        )}
        {canStop && (
          <button
            type="button"
            className="task-action"
            disabled={pending}
            aria-label={`Stop ${commitment.title} from this day on`}
            onClick={() => {
              const previous = commitment.stop_date
              void run(
                () => stopFocusSeries(commitment.entry_id, planDate),
                `${commitment.title} stops from this day; earlier days keep it`,
                // Undo puts back the end the series had (none, usually).
                () => stopFocusSeries(commitment.entry_id, previous),
              )
            }}
          >
            <CalendarX size={15} aria-hidden="true" />
            Stop from here
          </button>
        )}
        <button
          type="button"
          className="task-action"
          disabled={pending}
          aria-label={
            repeat ? `Remove every ${commitment.title}` : `Remove ${commitment.title}`
          }
          onClick={() =>
            void run(
              () => removeFocusEntry(commitment.entry_id),
              repeat ? 'Repeating commitment removed' : 'Commitment removed',
              () => restoreFocusEntry(commitment.entry_id),
            )
          }
        >
          <X size={15} aria-hidden="true" />
          {repeat ? 'Remove series' : 'Remove'}
        </button>
      </div>
    </li>
  )
}

/**
 * The Undo on a desktop toast (#429), matching the phone's undo bar: one click
 * runs the inverse write, then refetches. A refused undo (the state moved on)
 * gets its own error toast. Built from page-level callbacks only, so it still
 * works after the row that armed it has left the timeline.
 */
function useUndoAction(onMutated: () => void): (undo: () => Promise<unknown>) => ToastAction {
  const { withToast } = useToast()
  return (undo) => ({
    label: 'Undo',
    onAction: () => fireAndForget(withToast(undo(), { success: 'Undone' }).then(onMutated)),
  })
}

// A skipped occurrence of a repeating commitment (#429): muted and struck
// through at its time, holding none of the day. Its one verb brings it back by
// removing the skip, the same write as the Undo right after skipping.
function SkippedCommitmentRow({
  commitment,
  past,
  current,
  planDate,
  onMutated,
}: {
  commitment: SkippedCommitmentBlock
  past: boolean
  current: boolean
  planDate: string
  onMutated: () => void
}) {
  const { withToast } = useToast()
  const [pending, setPending] = useState(false)
  const undoAction = useUndoAction(onMutated)

  async function unskip(): Promise<void> {
    if (pending) return
    setPending(true)
    try {
      await withToast(removeFocusEntry(commitment.skip_entry_id), {
        success: `${commitment.title} is back on this day`,
        // Undo skips it again: the skip row is restored.
        action: () => undoAction(() => restoreFocusEntry(commitment.skip_entry_id)),
      })
      onMutated()
    } catch {
      // The error toast already surfaced; keep the row interactive to retry.
    } finally {
      setPending(false)
    }
  }

  return (
    <li
      className={[
        'focus-block focus-commitment focus-skipped',
        past && 'focus-block-past',
        current && 'focus-block-current',
      ]
        .filter(Boolean)
        .join(' ')}
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
          <span className="focus-skipped-word">skipped</span>
          {commitment.repeat_interval && (
            <span className="focus-repeat-word">
              {formatCommitmentRepeat(commitment.repeat_interval)}
            </span>
          )}
        </div>
      </div>
      <div className="focus-row-actions">
        <button
          type="button"
          className="task-action"
          disabled={pending}
          aria-label={`Un-skip ${commitment.title} on this day`}
          onClick={() => void unskip()}
        >
          <Undo2 size={15} aria-hidden="true" />
          Un-skip
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
  const [repeat, setRepeat] = useState<CommitmentRepeat>('once')
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
      const interval = commitmentRepeatInterval(repeat)
      await withToast(
        addFocusCommitment(planDate, title.trim(), start, minutes, interval),
        {
          success: interval
            ? `Added ${title.trim()} at ${start}, ${formatCommitmentRepeat(interval)}`
            : `Added ${title.trim()} at ${start}`,
        },
      )
      setTitle('')
      setStart('')
      setRepeat('once')
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
      <select
        aria-label="Commitment repeat"
        value={repeat}
        onChange={(event) => setRepeat(event.target.value as CommitmentRepeat)}
      >
        {COMMITMENT_REPEATS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
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

  // Reordering floating pins (#375). The drag only highlights the slot it is
  // over; the timeline is in clock order, so the new order shows after the
  // save, re-packed by the server, rather than as a local preview.
  const pinOrder = plan ? floatingPinOrder(plan.scheduled) : []
  const [draggedPin, setDraggedPin] = useState<number | null>(null)
  const [dropPinTarget, setDropPinTarget] = useState<number | null>(null)
  const [focusPin, setFocusPin] = useState<number | null>(null)
  const savingPins = useRef(false)

  async function savePinOrder(next: number[] | null, focusAfter: number | null) {
    if (!plan || next === null || savingPins.current) return
    savingPins.current = true
    setFocusPin(focusAfter)
    try {
      await withToast(reorderFocusPins(plan.date, next), { success: 'Pins reordered' })
      refetch()
    } catch {
      // The error toast already surfaced; the order on screen is unchanged.
    } finally {
      savingPins.current = false
    }
  }

  function pinReorder(block: ScheduledBlock): PinReorder | null {
    const entryId = block.entry_id
    if (entryId === null || pinOrder.length < 2 || !pinOrder.includes(entryId)) return null
    return {
      entryId,
      dragging: draggedPin === entryId,
      dropTarget: dropPinTarget === entryId && draggedPin !== entryId,
      focusGrip: focusPin === entryId,
      onDragStart: (id) => {
        setDraggedPin(id)
        setFocusPin(null)
      },
      onDragOver: (id) => setDropPinTarget(id),
      onDrop: (id) => {
        const dragged = draggedPin
        setDraggedPin(null)
        setDropPinTarget(null)
        if (dragged !== null) fireAndForget(savePinOrder(dropPin(pinOrder, dragged, id), null))
      },
      onDragEnd: () => {
        setDraggedPin(null)
        setDropPinTarget(null)
      },
      onKeyMove: (id, delta) => fireAndForget(savePinOrder(shiftPin(pinOrder, id, delta), id)),
    }
  }

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
  const timeline = plan ? timelineItems(plan.scheduled, plan.commitments, plan.skipped_commitments) : []
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
                        current={index === nowIndex}
                        planDate={plan.date}
                        onMutated={refetch}
                        onSkip={(id, title) => setSkipTarget({ id, title })}
                        reorder={pinReorder(item.block)}
                      />
                    ) : item.kind === 'skipped' ? (
                      <SkippedCommitmentRow
                        commitment={item.commitment}
                        past={nowIndex !== null && index < nowIndex}
                        current={index === nowIndex}
                        planDate={plan.date}
                        onMutated={refetch}
                      />
                    ) : (
                      <CommitmentRow
                        commitment={item.commitment}
                        past={nowIndex !== null && index < nowIndex}
                        current={index === nowIndex}
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
