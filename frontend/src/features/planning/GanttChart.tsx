import { type CSSProperties, Fragment, useMemo } from 'react'
import { Link } from 'react-router-dom'
import type { GanttModel } from './ganttModel'
import { addDays } from './ganttModel'

// The custom CSS-grid Gantt renderer for the read-only planning slice. No
// third-party library (the frappe-gantt attempt was abandoned): one grid spans
// the whole chart so the time axis, day backgrounds, today marker, and bars all
// share the same columns. Bar geometry comes pre-resolved from `buildGanttModel`
// — this component only places it. Read-only: no drag, no resize (later slices).

/** Whole-day difference `b - a` (UTC math, timezone-safe). */
function dayDiff(aIso: string, bIso: string): number {
  const [ay, am, ad] = aIso.split('-').map(Number)
  const [by, bm, bd] = bIso.split('-').map(Number)
  const a = Date.UTC(ay, am - 1, ad)
  const b = Date.UTC(by, bm - 1, bd)
  return Math.round((b - a) / 86_400_000)
}

/** Today as a local `YYYY-MM-DD`, matching the stored date strings. */
function todayISO(): string {
  const now = new Date()
  const y = now.getFullYear()
  const m = String(now.getMonth() + 1).padStart(2, '0')
  const d = String(now.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

const SHORT_MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

interface DayCell {
  iso: string
  dayOfMonth: number
  /** Month label shown on the first cell of each month (and the very first day). */
  monthLabel: string | null
  isWeekend: boolean
}

/** The single most attention-worthy status class for a bar's fill. */
function barTone(bar: GanttModel['bars'][number]): string {
  if (bar.isBlocking) return 'tone-blocking'
  if (bar.isBlocked) return 'tone-blocked'
  if (bar.workflowStatus === 'in_progress') return 'tone-in-progress'
  return 'tone-open'
}

export function GanttChart({ model }: { model: GanttModel }) {
  const { bars, unscheduled } = model

  const axis = useMemo(() => {
    if (bars.length === 0) return null
    let start = bars[0].start
    let end = bars[0].end
    for (const bar of bars) {
      if (bar.start < start) start = bar.start
      if (bar.end > end) end = bar.end
    }
    const count = dayDiff(start, end) + 1
    const days: DayCell[] = []
    for (let i = 0; i < count; i += 1) {
      const iso = addDays(start, i)
      const [, m, d] = iso.split('-').map(Number)
      const dow = new Date(`${iso}T00:00:00Z`).getUTCDay()
      days.push({
        iso,
        dayOfMonth: d,
        monthLabel: i === 0 || d === 1 ? SHORT_MONTHS[m - 1] : null,
        isWeekend: dow === 0 || dow === 6,
      })
    }
    const today = todayISO()
    const todayIdx = today >= start && today <= end ? dayDiff(start, today) : -1
    return { start, days, todayIdx }
  }, [bars])

  if (!axis) return null

  const { start, days, todayIdx } = axis
  const colCount = days.length

  return (
    <div className="gantt-wrap">
      <div
        className="gantt"
        style={{ '--gantt-cols': colCount } as CSSProperties}
        role="table"
        aria-label="Project timeline"
      >
        {/* Header: corner + one cell per day */}
        <div className="gantt-corner" style={{ gridColumn: 1, gridRow: 1 }}>
          Task
        </div>
        {days.map((day, i) => (
          <div
            key={day.iso}
            className={`gantt-day-head${day.isWeekend ? ' is-weekend' : ''}${
              i === todayIdx ? ' is-today' : ''
            }`}
            style={{ gridColumn: i + 2, gridRow: 1 }}
          >
            {day.monthLabel && <span className="gantt-month">{day.monthLabel}</span>}
            <span className="gantt-dom">{day.dayOfMonth}</span>
          </div>
        ))}

        {/* Background day columns (vertical grid lines, weekend + today shading) */}
        {days.map((day, i) => (
          <div
            key={`bg-${day.iso}`}
            className={`gantt-col-bg${day.isWeekend ? ' is-weekend' : ''}${
              i === todayIdx ? ' is-today' : ''
            }`}
            style={{ gridColumn: i + 2, gridRow: '2 / -1' }}
            aria-hidden="true"
          />
        ))}

        {/* One row per bar: label cell + positioned bar (+ due marker) */}
        {bars.map((bar, r) => {
          const offset = dayDiff(start, bar.start)
          const len = dayDiff(bar.start, bar.end) + 1
          const dueIdx =
            bar.dueDate && bar.dueDate >= start ? dayDiff(start, bar.dueDate) : -1
          const tooltip = `${bar.name} · ${bar.start} → ${bar.end}${
            bar.dueDate ? ` · due ${bar.dueDate}` : ''
          }`
          return (
            <Fragment key={bar.id}>
              <div
                className="gantt-label"
                style={{ gridColumn: 1, gridRow: r + 2, paddingLeft: 8 + bar.depth * 16 }}
                role="rowheader"
                title={bar.name}
              >
                {bar.depth > 0 && <span className="gantt-label-tick" aria-hidden="true" />}
                <span className="gantt-label-text">{bar.name}</span>
              </div>
              {dueIdx >= 0 && dueIdx < colCount && (
                <div
                  className="gantt-due"
                  style={{ gridColumn: dueIdx + 2, gridRow: r + 2 }}
                  aria-hidden="true"
                />
              )}
              <Link
                to={`/tasks/${bar.id}`}
                className={`gantt-bar ${barTone(bar)}${bar.conflict ? ' is-conflict' : ''}`}
                style={{ gridColumn: `${offset + 2} / span ${len}`, gridRow: r + 2 }}
                title={tooltip}
              >
                <span className="gantt-bar-text">{bar.name}</span>
              </Link>
            </Fragment>
          )
        })}
      </div>

      {unscheduled.length > 0 && (
        <aside className="gantt-unscheduled" aria-label="Unscheduled tasks">
          <h3>Unscheduled</h3>
          <p className="gantt-unscheduled-hint">No start or due date yet.</p>
          <ul>
            {unscheduled.map((task) => (
              <li key={task.id}>
                <Link to={`/tasks/${task.id}`} className="gantt-unscheduled-item">
                  <span className="gantt-unscheduled-title">{task.name}</span>
                  {task.isBlocking && <span className="gantt-flag flag-blocking">Blocking</span>}
                  {task.isBlocked && <span className="gantt-flag flag-blocked">Blocked</span>}
                </Link>
              </li>
            ))}
          </ul>
        </aside>
      )}
    </div>
  )
}
