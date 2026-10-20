import { useState } from 'react'
import { BottomSheet } from '../../../components/BottomSheet'
import { formatClockDuration, formatClockTime } from './focusDay'
import type { RunState } from './focusDay'
import type { CommitmentRepeatInterval } from '../../../types/focus'
import {
  COMMITMENT_REPEATS,
  commitmentRepeatInterval,
  formatCommitmentRepeat,
  type CommitmentRepeat,
} from '../commitmentRepeat'

/* The M05i bottom sheets. Both use the shared `BottomSheet` primitive, so
   they inherit its scrim, focus containment, Escape and swipe-down dismiss. */

interface Option {
  label: string
  selected: boolean
  onPick: () => void
}

function OptionGroup({ label, options }: { label: string; options: Option[] }) {
  return (
    <div className="focus-sheet-group">
      <h4>{label}</h4>
      <div className="focus-sheet-options">
        {options.map((option) => (
          <button
            key={option.label}
            type="button"
            className="focus-sheet-chip"
            aria-pressed={option.selected}
            onClick={option.onPick}
          >
            <span className="focus-chip-dot" aria-hidden="true" />
            {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}

const START_PRESETS = ['08:00', '09:00', '13:00']
const CAPACITY_PRESETS = [180, 360, 480]

interface SessionSheetProps {
  date: string
  today: string
  tomorrow: string
  startTime: string
  capacityMinutes: number
  onDate: (date: string) => void
  onStartTime: (startTime: string) => void
  onCapacity: (minutes: number) => void
  onAddCommitment: (
    title: string,
    start: string,
    minutes: number,
    repeat: CommitmentRepeatInterval | null,
  ) => void
  onClose: () => void
}

const COMMITMENT_MINUTES = [15, 30, 45, 60, 90, 120]

/**
 * Block fixed time for something that is not a task (a meeting). Lives in the
 * session sheet: creating is scoped to this lane, never a global button.
 */
function CommitmentForm({
  onAdd,
}: {
  onAdd: (title: string, start: string, minutes: number, repeat: CommitmentRepeatInterval | null) => void
}) {
  const [title, setTitle] = useState('')
  const [start, setStart] = useState('')
  const [minutes, setMinutes] = useState(30)
  const [repeat, setRepeat] = useState<CommitmentRepeat>('once')
  return (
    <form
      className="focus-sheet-group focus-sheet-commitment"
      aria-label="Add commitment"
      onSubmit={(event) => {
        event.preventDefault()
        if (title.trim() && start) {
          onAdd(title.trim(), start, minutes, commitmentRepeatInterval(repeat))
        }
      }}
    >
      <h4>Add commitment</h4>
      <input
        type="text"
        aria-label="Commitment title"
        placeholder="Standup"
        maxLength={200}
        required
        value={title}
        onChange={(event) => setTitle(event.target.value)}
      />
      <div className="focus-commitment-when">
        <input
          type="time"
          aria-label="Commitment start"
          required
          value={start}
          onChange={(event) => setStart(event.target.value)}
        />
        <select
          aria-label="Commitment length"
          value={minutes}
          onChange={(event) => setMinutes(Number(event.target.value))}
        >
          {COMMITMENT_MINUTES.map((value) => (
            <option key={value} value={value}>
              {formatClockDuration(value)}
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
        <button type="submit" className="focus-sheet-chip">
          Add
        </button>
      </div>
    </form>
  )
}

/**
 * Session window: day, start time, capacity. Changing any value re-times the
 * whole day — every row shifts and the tail resizes.
 *
 * The presets are the design's; the live value is appended when it isn't one of
 * them, the same trick the desktop capacity select uses. That matters here
 * because start time defaults to "now", which is almost never a preset — the
 * chip row would otherwise show the session starting at a time it isn't.
 */
export function FocusSessionSheet({
  date,
  today,
  tomorrow,
  startTime,
  capacityMinutes,
  onDate,
  onStartTime,
  onCapacity,
  onAddCommitment,
  onClose,
}: SessionSheetProps) {
  const startOptions = START_PRESETS.includes(startTime)
    ? START_PRESETS
    : [...START_PRESETS, startTime].sort()
  const capacityOptions = CAPACITY_PRESETS.includes(capacityMinutes)
    ? CAPACITY_PRESETS
    : [...CAPACITY_PRESETS, capacityMinutes].sort((a, b) => a - b)

  return (
    <BottomSheet className="focus-sheet" labelledBy="focus-session-sheet-title" handleLabel="Close session window" onClose={onClose}>
      <header className="focus-sheet-head">
        <h2 id="focus-session-sheet-title">Session window</h2>
        <button type="button" className="focus-sheet-done" onClick={onClose}>
          Done
        </button>
      </header>
      <OptionGroup
        label="Day"
        options={[
          { label: 'Today', selected: date === today, onPick: () => onDate(today) },
          { label: 'Tomorrow', selected: date === tomorrow, onPick: () => onDate(tomorrow) },
        ]}
      />
      <OptionGroup
        label="Start time"
        options={startOptions.map((value) => ({
          label: value,
          selected: value === startTime,
          onPick: () => onStartTime(value),
        }))}
      />
      <OptionGroup
        label="Capacity"
        options={capacityOptions.map((value) => ({
          label: formatClockDuration(value),
          selected: value === capacityMinutes,
          onPick: () => onCapacity(value),
        }))}
      />
      <CommitmentForm onAdd={onAddCommitment} />
    </BottomSheet>
  )
}

export interface RowSheetTarget {
  taskId: number
  title: string
  start: number
  duration: number
  planned: number
  isCurrent: boolean
  isRecurring: boolean
  /** A finished row is a receipt: the only thing left to do with it is undo it. */
  isDone: boolean
  /** Pinned into the day (#319), so the sheet offers Unpin. */
  isPinned: boolean
  /** A floating pin with another above / below it (#375): Move up / Move down. */
  canMoveUp: boolean
  canMoveDown: boolean
}

interface RowSheetProps {
  target: RowSheetTarget
  run: RunState
  onStart: () => void
  onPause: () => void
  /** Mark done, or reopen when the row is already a receipt. */
  onDone: () => void
  onDefer: () => void
  onSkip: () => void
  onUnpin: () => void
  /** Reorder a floating pin: -1 moves it up, +1 down. */
  onMove: (delta: number) => void
  onClose: () => void
}

/**
 * The row's ⋯ sheet — the complete, accessible path to everything the gesture
 * and the clock chip do. The gesture is layered on top of this, never instead
 * of it, and the state verb appears here because the chip that carries it on
 * the band reads as a status readout.
 */
export function FocusRowSheet({ target, run, onStart, onPause, onDone, onDefer, onSkip, onUnpin, onMove, onClose }: RowSheetProps) {
  return (
    <BottomSheet className="focus-sheet focus-row-sheet" labelledBy="focus-row-sheet-title" handleLabel="Close block actions" onClose={onClose}>
      <header className="focus-sheet-head focus-row-sheet-head">
        <h2 id="focus-row-sheet-title">{target.title}</h2>
        <span>
          {formatClockTime(target.start)} → {formatClockTime(target.start + target.duration)} ·{' '}
          {formatClockDuration(target.planned)}
        </span>
      </header>
      <div className="focus-sheet-actions">
        {target.isDone ? (
          <button type="button" className="focus-sheet-action" onClick={onDone}>
            Reopen
          </button>
        ) : (
          <>
        {target.isCurrent &&
          (run === 'running' ? (
            <button type="button" className="focus-sheet-action" onClick={onPause}>
              Pause
            </button>
          ) : (
            <button type="button" className="focus-sheet-action" onClick={onStart}>
              {run === 'paused' ? 'Resume' : 'Start'}
            </button>
          ))}
        <button type="button" className="focus-sheet-action" onClick={onDone}>
          Mark done
        </button>
        <button type="button" className="focus-sheet-action" onClick={onDefer}>
          Defer to tomorrow
        </button>
        {target.canMoveUp && (
          <button type="button" className="focus-sheet-action" onClick={() => onMove(-1)}>
            Move up
          </button>
        )}
        {target.canMoveDown && (
          <button type="button" className="focus-sheet-action" onClick={() => onMove(1)}>
            Move down
          </button>
        )}
        {target.isPinned && (
          <button type="button" className="focus-sheet-action" onClick={onUnpin}>
            Unpin
          </button>
        )}
        {target.isRecurring && (
          <button type="button" className="focus-sheet-action danger" onClick={onSkip}>
            Skip occurrence
          </button>
        )}
          </>
        )}
      </div>
      <button type="button" className="focus-sheet-cancel" onClick={onClose}>
        Cancel
      </button>
    </BottomSheet>
  )
}

interface CommitmentSheetProps {
  title: string
  start: number
  duration: number
  /** Set when the commitment repeats (#376): Move and Remove then act on the series. */
  repeat: CommitmentRepeatInterval | null
  onMove: (start: string) => void
  onSkip: () => void
  onRemove: () => void
  onClose: () => void
}

/**
 * A commitment's ⋯ sheet: it is not a task, so Move and Remove are all it
 * offers, plus Skip this day when it repeats.
 */
export function FocusCommitmentSheet({ title, start, duration, repeat, onMove, onSkip, onRemove, onClose }: CommitmentSheetProps) {
  const [moveTo, setMoveTo] = useState(formatClockTime(start))
  return (
    <BottomSheet className="focus-sheet focus-row-sheet" labelledBy="focus-commitment-sheet-title" handleLabel="Close commitment actions" onClose={onClose}>
      <header className="focus-sheet-head focus-row-sheet-head">
        <h2 id="focus-commitment-sheet-title">{title}</h2>
        <span>
          {formatClockTime(start)} → {formatClockTime(start + duration)} · {formatClockDuration(duration)}
          {repeat && (
            <>
              {' · '}
              <span className="focus-repeat-word">{formatCommitmentRepeat(repeat)}</span>
            </>
          )}
        </span>
      </header>
      <form
        className="focus-sheet-group focus-commitment-when"
        onSubmit={(event) => {
          event.preventDefault()
          if (moveTo) onMove(moveTo)
        }}
      >
        <input
          type="time"
          aria-label={`Move ${title} to`}
          required
          value={moveTo}
          onChange={(event) => setMoveTo(event.target.value)}
        />
        <button type="submit" className="focus-sheet-chip">
          {repeat ? 'Move every day' : 'Move'}
        </button>
      </form>
      <div className="focus-sheet-actions">
        {repeat && (
          <button type="button" className="focus-sheet-action" onClick={onSkip}>
            Skip this day
          </button>
        )}
        <button type="button" className="focus-sheet-action danger" onClick={onRemove}>
          {repeat ? 'Remove series' : 'Remove'}
        </button>
      </div>
      <button type="button" className="focus-sheet-cancel" onClick={onClose}>
        Cancel
      </button>
    </BottomSheet>
  )
}
