import { useState } from 'react'
import { addDaysISO, dueStatus, formatDueDate, parseLocalDate, todayISO } from '../../../utils/dates'
import { ChipPopover } from './ChipPopover'
import { buildMonthGrid } from './monthGrid'

const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa']

interface Props {
  value: string | null
  onChange: (next: string | null) => void
  disabled?: boolean
  disabledHint?: string
}

interface EditorProps {
  value: string | null
  onCommit: (next: string | null) => void
  close: () => void
}

/** Mounted fresh each time the popover opens, so the view month starts current. */
function DueDateEditor({ value, onCommit, close }: EditorProps) {
  const [view, setView] = useState(() => {
    const base = value ? parseLocalDate(value) : new Date()
    return { year: base.getFullYear(), month: base.getMonth() }
  })
  const today = todayISO()
  const weeks = buildMonthGrid(view.year, view.month)
  const monthLabel = new Date(view.year, view.month, 1).toLocaleDateString(undefined, {
    month: 'long',
    year: 'numeric',
  })

  function pick(next: string | null) {
    close()
    if (next !== value) onCommit(next)
  }

  function shiftMonth(delta: number) {
    setView((v) => {
      const base = new Date(v.year, v.month + delta, 1)
      return { year: base.getFullYear(), month: base.getMonth() }
    })
  }

  return (
    <div className="due-editor">
      <div className="due-presets">
        <button type="button" onClick={() => pick(today)}>
          Today
        </button>
        <button type="button" onClick={() => pick(addDaysISO(today, 1))}>
          Tomorrow
        </button>
        <button type="button" onClick={() => pick(addDaysISO(today, 7))}>
          Next week
        </button>
        {value !== null && (
          <button type="button" onClick={() => pick(null)}>
            Clear
          </button>
        )}
      </div>
      <div className="due-cal-header">
        <button type="button" aria-label="Previous month" onClick={() => shiftMonth(-1)}>
          ‹
        </button>
        <span>{monthLabel}</span>
        <button type="button" aria-label="Next month" onClick={() => shiftMonth(1)}>
          ›
        </button>
      </div>
      <div className="due-cal-grid">
        {WEEKDAYS.map((day) => (
          <span key={day} className="due-cal-weekday" aria-hidden="true">
            {day}
          </span>
        ))}
        {weeks.flat().map((iso, i) =>
          iso === null ? (
            <span key={i} />
          ) : (
            <button
              key={i}
              type="button"
              className={`due-day${iso === today ? ' is-today' : ''}`}
              aria-label={iso}
              aria-current={iso === value ? 'date' : undefined}
              onClick={() => pick(iso)}
            >
              {Number(iso.slice(8))}
            </button>
          ),
        )}
      </div>
    </div>
  )
}

export function DueDateChip({ value, onChange, disabled, disabledHint }: Props) {
  const empty = value === null
  return (
    <ChipPopover
      chip={empty ? 'Set due date' : `Due ${formatDueDate(value)}`}
      chipClassName={empty ? 'due due-none chip-empty' : `due due-${dueStatus(value)}`}
      label={empty ? 'Set due date' : `Due date: ${formatDueDate(value)}`}
      disabled={disabled}
      disabledHint={disabledHint}
    >
      {(close) => <DueDateEditor value={value} onCommit={onChange} close={close} />}
    </ChipPopover>
  )
}
