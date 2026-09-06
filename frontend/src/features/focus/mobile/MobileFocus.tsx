import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  CalendarClock,
  Check,
  ChevronDown,
  ChevronRight,
  Clock3,
  MoreHorizontal,
  Pause,
  Play,
  Plus,
} from 'lucide-react'
import { apiErrorMessage } from '../../../api/errorMessage'
import { markTaskDone, reopenTask, updateTask } from '../../../api/tasks'
import { addDaysISO } from '../../../utils/dates'
import { useTaskLinkTo } from '../../tasks/panel/taskPanelContext'
import type { FocusPlan } from '../../../types/focus'
import { FocusRowSheet, FocusSessionSheet, type RowSheetTarget } from './FocusSheets'
import { SwipeRow } from './SwipeRow'
import { useFocusClock } from './useFocusClock'
import { useFocusSessionLog } from './useFocusSessionLog'
import {
  UNDO_LIFETIME_MS,
  blockingCounts,
  buildDayShape,
  formatClockDuration,
  formatClockTime,
  rowContext,
  rowSignal,
  sessionStartMinutes,
  tailHeight,
  type DayRow,
} from './focusDay'

/* M05i — /focus at phone width.
 *
 * A vertical timeline in which duration is height: a 2h block is twice the
 * height of a 1h block, the gutter's hairlines land on real clock boundaries,
 * and the dashed tail is capacity not yet spent. The current block is a band
 * inside the timeline rather than a card above it, and it carries no action
 * buttons at all — ending it is a swipe, starting and pausing it is a tap on
 * the remaining-time readout, and ⋯ is the only persistent control.
 *
 * Divergences from the prototype, all of them where the design's local state
 * meets a real service layer:
 *
 *  - Deferring writes `deferred_until` (the existing verb, same as desktop), so
 *    a deferred block leaves the day rather than landing in "Didn't fit". The
 *    sheet says "Defer to tomorrow" because that is what happens. The undo bar
 *    is what makes the gesture recoverable.
 *  - "Didn't fit" is the plan's overflow — work that didn't fit the capacity
 *    you set. Scheduling one therefore extends the session to fit it and
 *    everything ranked above it, which is the only honest way to pull an item
 *    into a deterministically packed day. Pinning would need a stored plan.
 *  - Completing a block records its actual minutes in the session log, not on
 *    the server: there is no column for it yet.
 */

const MAX_CAPACITY_MINUTES = 1440

interface Props {
  plan: FocusPlan | null
  loading: boolean
  error: string | null
  date: string
  today: string
  startTime: string
  /** The session's resolved capacity, whichever mode produced it. */
  capacity: number
  setDate: (date: string) => void
  setStartTime: (startTime: string) => void
  setCapacityMinutes: (minutes: number) => void
  /** Tells the plan hook how much of the window finished work has already eaten. */
  setConsumedMinutes: (minutes: number) => void
  refetch: () => void
  onSkip: (taskId: number, title: string) => void
}

interface PendingUndo {
  label: string
  run: () => Promise<void>
}

export function MobileFocus({
  plan,
  loading,
  error,
  date,
  today,
  startTime,
  capacity,
  setDate,
  setStartTime,
  setCapacityMinutes,
  setConsumedMinutes,
  refetch,
  onSkip,
}: Props) {
  const taskLinkTo = useTaskLinkTo()
  const log = useFocusSessionLog(date)
  const scheduled = plan?.scheduled ?? []
  // Done work leaves the plan, so the first remaining block is always the
  // current one. Null once the day is clear.
  const currentTaskId = scheduled.length ? scheduled[0].task_id : null
  const clock = useFocusClock(currentTaskId, date)

  const [sessionOpen, setSessionOpen] = useState(false)
  const [rowTarget, setRowTarget] = useState<RowSheetTarget | null>(null)
  const [openFit, setOpenFit] = useState(false)
  const [openBlocked, setOpenBlocked] = useState(false)
  const [undo, setUndo] = useState<PendingUndo | null>(null)
  const [mutationError, setMutationError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const undoTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // The request window starts where finished work ended and asks for what is
  // left of the capacity, so the scheduler refills the rest of the day instead
  // of planning a fresh full one on top of the part already spent. A completion
  // therefore issues two requests — `mutate`'s refetch, then this shift — and
  // the plan hook discards the first. Cheap against a local endpoint, and much
  // less fragile than making one path's refetch conditional on the other.
  useEffect(() => setConsumedMinutes(log.spent), [log.spent, setConsumedMinutes])
  useEffect(() => () => { if (undoTimer.current) clearTimeout(undoTimer.current) }, [])

  const armUndo = useCallback((label: string, run: () => Promise<void>) => {
    if (undoTimer.current) clearTimeout(undoTimer.current)
    setUndo({ label, run })
    undoTimer.current = setTimeout(() => setUndo(null), UNDO_LIFETIME_MS)
  }, [])

  const dismissUndo = useCallback(() => {
    if (undoTimer.current) clearTimeout(undoTimer.current)
    setUndo(null)
  }, [])

  const mutate = useCallback(
    async (action: () => Promise<void>) => {
      if (pending) return
      setPending(true)
      setMutationError(null)
      try {
        await action()
        refetch()
      } catch (reason) {
        // The backend's reason, not "API error 409": a refused write here is
        // usually a rule the row can explain (a parent is completed by its
        // subtasks, a dependency is unfinished).
        setMutationError(apiErrorMessage(reason, 'That change did not stick'))
      } finally {
        setPending(false)
      }
    },
    [pending, refetch],
  )

  const dayStart = sessionStartMinutes(startTime)
  const shape = buildDayShape({
    finished: log.finished,
    scheduled,
    dayStart,
    capacity,
    elapsed: clock.elapsed,
  })
  const { rows, currentIndex, dayEnd, spent, free, overage, shift } = shape
  const blocking = blockingCounts(plan?.blocked ?? [])
  const overflow = plan?.overflow ?? []
  const blocked = plan?.blocked ?? []
  const dayClear = currentIndex < 0
  const capacityEnd = dayStart + capacity

  /** Mark a block done: the clock's minutes when it was timed, the estimate when it wasn't. */
  async function markDone(row: DayRow): Promise<void> {
    const timed = row.kind === 'current' && (clock.run !== 'idle' || clock.elapsed > 0)
    // The prototype books `max(1, elapsed)` unconditionally, which would charge
    // an untimed 2h block one minute and collapse the day's arithmetic. An
    // untimed block costs what it was planned at.
    const minutes = timed ? Math.max(1, clock.elapsed) : row.planned
    const snapshot = clock.snapshot()
    await mutate(async () => {
      await markTaskDone(row.taskId)
      log.record({ taskId: row.taskId, title: row.title, minutes, plannedMinutes: row.planned })
      // Only the block that owns the clock ends it. Completing a later row from
      // its ⋯ sheet must not throw away the time logged against the one you are
      // actually in — the same guard `defer` has always had.
      if (row.kind === 'current') clock.reset()
      armUndo(`Marked done · ${row.title}`, async () => {
        // Reopening leaves any occurrence the recurrence roll-forward created,
        // but that roll-forward is idempotent — completing again reuses it.
        await reopenTask(row.taskId)
        log.remove(row.taskId)
        clock.restore(snapshot)
      })
    })
  }

  /** Defer a block out of the day. `deferred_until` is the existing verb; undo clears it. */
  async function defer(row: DayRow): Promise<void> {
    const snapshot = clock.snapshot()
    await mutate(async () => {
      await updateTask(row.taskId, { deferred_until: addDaysISO(plan?.date ?? date, 1) })
      if (row.kind === 'current') clock.reset()
      armUndo(`Deferred · ${row.title}`, async () => {
        await updateTask(row.taskId, { deferred_until: null })
        clock.restore(snapshot)
      })
    })
  }

  async function reopen(row: DayRow): Promise<void> {
    await mutate(async () => {
      await reopenTask(row.taskId)
      log.remove(row.taskId)
    })
  }

  /**
   * Pull the nth overflow item onto the timeline by extending the session to
   * hold it. The packer is greedy in rank order, so fitting item n means fitting
   * everything above it too — they outrank it, and the capacity bar says so
   * immediately.
   */
  function schedule(index: number): void {
    const needed = overflow
      .slice(0, index + 1)
      .reduce((total, task) => total + task.estimated_minutes, 0)
    const used = plan?.used_minutes ?? 0
    setCapacityMinutes(Math.min(MAX_CAPACITY_MINUTES, log.spent + used + needed))
  }

  function openRowSheet(row: DayRow): void {
    setRowTarget({
      taskId: row.taskId,
      title: row.title,
      start: row.start,
      duration: row.duration,
      planned: row.planned,
      isCurrent: row.kind === 'current',
      isRecurring: row.block?.is_recurring ?? false,
      isDone: row.kind === 'done',
    })
  }

  const chipState = overage > 0 ? 'overrun' : clock.run
  const chipVerb = clock.run === 'running' ? 'Pause' : clock.run === 'paused' ? 'Resume' : 'Start'
  const current = currentIndex >= 0 ? rows[currentIndex] : null
  const remaining = current ? current.planned - clock.elapsed : 0
  const chipLabel =
    overage > 0
      ? `${formatClockDuration(overage)} over`
      : clock.run === 'idle'
        ? `${formatClockDuration(current?.planned ?? 0)} planned`
        : clock.run === 'paused'
          ? `Paused · ${formatClockDuration(Math.max(0, remaining))} left`
          : `${formatClockDuration(Math.max(0, remaining))} left`

  function renderRow(row: DayRow, index: number) {
    const isCurrent = index === currentIndex
    const spineFill = row.kind === 'done' ? 100 : isCurrent ? Math.min(100, (clock.elapsed / Math.max(1, row.duration)) * 100) : 0
    const signal = row.block ? rowSignal(row.block.priority, row.block.workflow_status, row.block.due_signal) : null
    const context = row.block ? rowContext(row.block, blocking.get(row.taskId) ?? 0) : ''
    const meta = signal ? (
      <span className="focus-row-meta">
        <span className={`focus-signal tone-${signal.tone}`}>{signal.label}</span>
        <span className="focus-meta-sep" aria-hidden="true">·</span>
        <span>{context}</span>
      </span>
    ) : (
      <span className="focus-row-meta">
        <span>{context}</span>
      </span>
    )
    const more = (
      <button
        type="button"
        className="focus-more"
        aria-label={`Actions for ${row.title}`}
        aria-haspopup="dialog"
        onClick={() => openRowSheet(row)}
      >
        <MoreHorizontal size={18} aria-hidden="true" />
      </button>
    )

    return (
      <div key={`${row.kind}-${row.taskId}`} className="focus-row" data-kind={row.kind} style={{ minHeight: `${row.height}px` }}>
        <div className="focus-gutter">
          <span className="focus-time">{formatClockTime(row.start)}</span>
          <span className="focus-spine">
            <span style={{ height: `${spineFill}%` }} />
          </span>
        </div>
        <SwipeRow enabled={row.kind !== 'done'} onDone={() => void markDone(row)} onDefer={() => void defer(row)}>
          {isCurrent ? (
            <div className="focus-band">
              <div className="focus-band-head">
                <span className="focus-now">Now</span>
                <button
                  type="button"
                  className="focus-clock-chip"
                  data-state={chipState}
                  title={chipVerb}
                  aria-label={`${chipVerb} ${row.title} — ${chipLabel}`}
                  onClick={() => (clock.run === 'running' ? clock.pause() : clock.start())}
                >
                  <span className="focus-clock-pill">
                    {clock.run === 'running' ? <Pause size={13} aria-hidden="true" /> : <Play size={13} aria-hidden="true" />}
                    {chipLabel}
                  </span>
                </button>
              </div>
              {/* Anchors are natively draggable, and the browser's own
                  drag-and-drop pre-empts the pointermove stream — a swipe that
                  started on the title would stall after a few pixels. */}
              <Link to={taskLinkTo(row.taskId)} className="focus-band-title" draggable={false}>
                {row.title}
              </Link>
              <div className="focus-band-foot">
                {meta}
                {more}
              </div>
            </div>
          ) : (
            <div className="focus-row-body">
              <span className="focus-ring" data-done={row.kind === 'done'}>
                {row.kind === 'done' && <Check size={13} aria-hidden="true" />}
              </span>
              <span className="focus-row-main">
                {row.kind === 'done' ? (
                  <span className="focus-row-title">{row.title}</span>
                ) : (
                  <Link to={taskLinkTo(row.taskId)} className="focus-row-title" draggable={false}>
                    {row.title}
                  </Link>
                )}
                {row.kind !== 'done' && meta}
              </span>
              {more}
            </div>
          )}
        </SwipeRow>
      </div>
    )
  }

  return (
    <div className="mobile-focus" data-state={chipState}>
      <div className="focus-heading">
        <h1>Focus</h1>
        <span
          className="focus-capacity-bar"
          role="progressbar"
          aria-label="Session capacity"
          aria-valuemin={0}
          aria-valuemax={capacity}
          aria-valuenow={plan ? Math.min(spent, capacity) : undefined}
        >
          <span className="cap-done" style={{ width: `${shape.segments.done}%` }} />
          <span className="cap-planned" style={{ width: `${shape.segments.planned}%` }} />
          <span className="cap-over" style={{ width: `${shape.segments.over}%` }} />
        </span>
        <span className="focus-capacity-caption">
          <span>Ends {formatClockTime(dayEnd)}</span>
          <span className="focus-meta-sep" aria-hidden="true">·</span>
          <span className={free < 0 ? 'over' : undefined}>
            {free >= 0 ? `${formatClockDuration(free)} free` : `${formatClockDuration(-free)} over capacity`}
          </span>
        </span>
      </div>

      <button
        type="button"
        className="focus-session-row"
        aria-haspopup="dialog"
        aria-expanded={sessionOpen}
        onClick={() => setSessionOpen(true)}
      >
        <CalendarClock size={16} aria-hidden="true" />
        <span>
          {date === today ? 'Today' : date === addDaysISO(today, 1) ? 'Tomorrow' : date} ·{' '}
          {formatClockTime(dayStart)} → {formatClockTime(capacityEnd)} · {formatClockDuration(capacity)} capacity
        </span>
        <ChevronDown size={16} aria-hidden="true" />
      </button>

      {loading && <p className="focus-loading">Preparing your focus session…</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {mutationError && <p role="alert" className="error">{mutationError}</p>}

      {!error && plan && (
        <>
          {dayClear && log.finished.length > 0 && (
            <section className="focus-day-clear" aria-labelledby="focus-day-clear-heading">
              <h2 id="focus-day-clear-heading">Day clear</h2>
              <span className="focus-clear-head">
                {log.finished.length} {log.finished.length === 1 ? 'block' : 'blocks'} done ·{' '}
                {formatClockDuration(spent)} spent
              </span>
              <span className="focus-clear-sub">
                Finished {formatClockTime(dayEnd)} · {formatClockDuration(Math.max(0, free))} of capacity left
              </span>
              <div className="focus-clear-actions">
                <button type="button" className="focus-clear-primary" onClick={() => setDate(addDaysISO(today, 1))}>
                  Plan tomorrow
                </button>
                <button type="button" className="focus-clear-ghost" onClick={() => setOpenFit((value) => !value)}>
                  {overflow.length ? `Reschedule ${overflow.length}` : 'Nothing left'}
                </button>
              </div>
            </section>
          )}

          {dayClear && log.finished.length === 0 && (
            <p className="focus-empty-line">
              {overflow.length > 0
                ? 'Nothing fit this session’s capacity — see what didn’t fit below.'
                : blocked.length > 0
                  ? 'Nothing schedulable — every open task is waiting on a dependency.'
                  : 'No open tasks to schedule for this day.'}
            </p>
          )}

          {shift > 0 && (
            <div className="focus-push-notice">
              <Clock3 size={15} aria-hidden="true" />
              <span>
                Ran {formatClockDuration(shift)} long — every block after it moved. The day now ends{' '}
                {formatClockTime(dayEnd)}.
              </span>
            </div>
          )}

          <div className="focus-timeline">
            {rows.map(renderRow)}
            <div className="focus-tail" data-over={free < 0} style={{ minHeight: `${tailHeight(Math.max(0, free))}px` }}>
              <div className="focus-gutter">
                <span className="focus-tail-times">
                  <span>{formatClockTime(free >= 0 ? dayEnd : capacityEnd)}</span>
                  <span>{formatClockTime(free >= 0 ? capacityEnd : dayEnd)}</span>
                </span>
                <span className="focus-tail-spine" />
              </div>
              <div className="focus-tail-body">
                <span className="focus-tail-label">
                  {free >= 0
                    ? `${formatClockDuration(free)} free`
                    : `${formatClockDuration(-free)} past capacity`}
                </span>
                {overflow.length > 0 && free > 0 && (
                  <button type="button" className="focus-pull" onClick={() => schedule(0)}>
                    <Plus size={16} aria-hidden="true" />
                    Pull from Didn’t fit · {overflow.length}
                  </button>
                )}
              </div>
            </div>
          </div>

          <section className="focus-foot">
            <button type="button" className="focus-disclosure" aria-expanded={openFit} onClick={() => setOpenFit((value) => !value)}>
              {openFit ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
              Didn’t fit · {overflow.length}
            </button>
            {openFit && (
              <ul className="focus-foot-list">
                {overflow.map((task, index) => (
                  <li key={task.task_id}>
                    <span className="focus-row-main">
                      <Link to={taskLinkTo(task.task_id)} className="focus-row-title">
                        {task.title}
                      </Link>
                      <span className="focus-row-meta">
                        {(() => {
                          const signal = rowSignal(task.priority, task.workflow_status, task.due_signal)
                          return signal ? (
                            <>
                              <span className={`focus-signal tone-${signal.tone}`}>{signal.label}</span>
                              <span className="focus-meta-sep" aria-hidden="true">·</span>
                            </>
                          ) : null
                        })()}
                        <span>
                          {task.estimate_assumed
                            ? `${formatClockDuration(task.estimated_minutes)} est.`
                            : formatClockDuration(task.estimated_minutes)}
                          {task.scheduled_subtask_count > 0 &&
                            ` · ${task.scheduled_subtask_count} ${task.scheduled_subtask_count === 1 ? 'subtask' : 'subtasks'} scheduled`}
                        </span>
                      </span>
                    </span>
                    <button type="button" className="focus-schedule" onClick={() => schedule(index)}>
                      Schedule
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <button type="button" className="focus-disclosure" aria-expanded={openBlocked} onClick={() => setOpenBlocked((value) => !value)}>
              {openBlocked ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
              Blocked · {blocked.length}
            </button>
            {openBlocked && (
              <ul className="focus-foot-list focus-blocked-foot">
                {blocked.map((task) => (
                  <li key={task.task_id}>
                    <span className="focus-row-main">
                      <Link to={taskLinkTo(task.task_id)} className="focus-row-title">
                        {task.title}
                      </Link>
                      <span className="focus-row-meta">
                        <span>
                          Waiting on{' '}
                          {task.blocking_tasks.length === 1
                            ? task.blocking_tasks[0].title
                            : `${task.blocking_tasks.length} tasks`}
                        </span>
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}

      {undo && (
        <div className="focus-undo" role="status">
          <span>{undo.label}</span>
          <button
            type="button"
            onClick={() => {
              const action = undo.run
              dismissUndo()
              void mutate(action)
            }}
          >
            Undo
          </button>
        </div>
      )}

      {sessionOpen && (
        <FocusSessionSheet
          date={date}
          today={today}
          tomorrow={addDaysISO(today, 1)}
          startTime={startTime}
          capacityMinutes={capacity}
          onDate={setDate}
          onStartTime={setStartTime}
          onCapacity={setCapacityMinutes}
          onClose={() => setSessionOpen(false)}
        />
      )}

      {rowTarget && (
        <FocusRowSheet
          target={rowTarget}
          run={clock.run}
          onStart={() => { clock.start(); setRowTarget(null) }}
          onPause={() => { clock.pause(); setRowTarget(null) }}
          onDone={() => {
            const row = rows.find((candidate) => candidate.taskId === rowTarget.taskId)
            setRowTarget(null)
            if (!row) return
            void (row.kind === 'done' ? reopen(row) : markDone(row))
          }}
          onDefer={() => {
            const row = rows.find((candidate) => candidate.taskId === rowTarget.taskId)
            setRowTarget(null)
            if (row) void defer(row)
          }}
          onSkip={() => {
            setRowTarget(null)
            onSkip(rowTarget.taskId, rowTarget.title)
          }}
          onClose={() => setRowTarget(null)}
        />
      )}
    </div>
  )
}
