import { BottomSheet } from '../../../components/BottomSheet'
import { formatClockDuration, formatClockTime } from './focusDay'
import type { RunState } from './focusDay'

/* The two M05i bottom sheets. Both use the shared `BottomSheet` primitive, so
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
  onClose: () => void
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
  onClose: () => void
}

/**
 * The row's ⋯ sheet — the complete, accessible path to everything the gesture
 * and the clock chip do. The gesture is layered on top of this, never instead
 * of it, and the state verb appears here because the chip that carries it on
 * the band reads as a status readout.
 */
export function FocusRowSheet({ target, run, onStart, onPause, onDone, onDefer, onSkip, onClose }: RowSheetProps) {
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
