import { addDays } from './ganttModel'
import type { GanttBar } from './ganttModel'

// The zoom axis for the Gantt (Slice 7). The renderer is one CSS grid keyed off
// `--gantt-cols`; a zoom level is just a different day->column bucketing of the
// same date-space bars. This module is the *only* place that bucketing lives:
// given the bars' date span and a zoom level it produces the ordered columns
// (each covering one or more whole days) plus a pure `columnOf(iso)` that maps any
// date to its column index. Bar placement in `GanttChart` goes through that map,
// so the same bars render at day, week, or month granularity with no change to
// `buildGanttModel` and no scheduling math (CLAUDE.md prime directive #1 — this is
// presentation, not planning).

export type ZoomLevel = 'day' | 'week' | 'month'

/** Days each column of a zoom level nominally represents (months vary; see below). */
export const NOMINAL_DAYS_PER_COLUMN: Record<ZoomLevel, number> = {
  day: 1,
  week: 7,
  month: 30,
}

const SHORT_MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

/** One rendered column: the days it covers plus its header labels/flags. */
export interface AxisColumn {
  /** The first calendar day this column covers (`YYYY-MM-DD`). */
  iso: string
  /** Inclusive last day this column covers — equals `iso` at day zoom. */
  endIso: string
  /** Primary header label (day-of-month, ISO week start, or month name). */
  label: string
  /** Secondary label shown on a boundary (month name at day/week zoom; year at month). */
  groupLabel: string | null
  /** True when the column covers a weekend (day zoom only; false otherwise). */
  isWeekend: boolean
}

export interface GanttAxis {
  /** The ordered columns spanning every bar, left to right. */
  columns: AxisColumn[]
  /** Map any date to its column index; clamped to `[0, columns.length - 1]`. */
  columnOf: (iso: string) => number
  /** The index of today's column, or -1 when today is outside the span. */
  todayIdx: number
  /** Whole days each column covers (1/7/~30) — the px<->day scale for gestures. */
  daysPerColumn: number
}

/** Whole-day difference `b - a` (UTC math, timezone-safe). */
export function dayDiff(aIso: string, bIso: string): number {
  const [ay, am, ad] = aIso.split('-').map(Number)
  const [by, bm, bd] = bIso.split('-').map(Number)
  const a = Date.UTC(ay, am - 1, ad)
  const b = Date.UTC(by, bm - 1, bd)
  return Math.round((b - a) / 86_400_000)
}

/** Today as a local `YYYY-MM-DD`, matching the stored date strings. */
export function todayISO(): string {
  const now = new Date()
  const y = now.getFullYear()
  const m = String(now.getMonth() + 1).padStart(2, '0')
  const d = String(now.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

/** UTC day-of-week for an ISO date (0 = Sunday … 6 = Saturday). */
function dow(iso: string): number {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay()
}

/** The Monday on or before `iso` (ISO weeks start Monday). */
function weekStart(iso: string): string {
  const d = dow(iso)
  // Sunday (0) is 6 days into an ISO week.
  const back = d === 0 ? 6 : d - 1
  return addDays(iso, -back)
}

/** The first of `iso`'s month. */
function monthStart(iso: string): string {
  return `${iso.slice(0, 7)}-01`
}

/** The min `start` and max `end` across the bars (caller guarantees non-empty). */
function spanOf(bars: GanttBar[]): { start: string; end: string } {
  let start = bars[0].start
  let end = bars[0].end
  for (const bar of bars) {
    if (bar.start < start) start = bar.start
    if (bar.end > end) end = bar.end
  }
  return { start, end }
}

function dayColumns(start: string, end: string): AxisColumn[] {
  const count = dayDiff(start, end) + 1
  const cols: AxisColumn[] = []
  for (let i = 0; i < count; i += 1) {
    const iso = addDays(start, i)
    const [, m, d] = iso.split('-').map(Number)
    cols.push({
      iso,
      endIso: iso,
      label: String(d),
      // Month name on the first cell of each month (and the very first day).
      groupLabel: i === 0 || d === 1 ? SHORT_MONTHS[m - 1] : null,
      isWeekend: dow(iso) === 0 || dow(iso) === 6,
    })
  }
  return cols
}

function weekColumns(start: string, end: string): AxisColumn[] {
  const cols: AxisColumn[] = []
  let cursor = weekStart(start)
  const last = weekStart(end)
  let prevMonth = ''
  while (cursor <= last) {
    const endIso = addDays(cursor, 6)
    const [, m, d] = cursor.split('-').map(Number)
    const month = SHORT_MONTHS[m - 1]
    cols.push({
      iso: cursor,
      endIso,
      label: `${month} ${d}`,
      // Year label when the month rolls over, to anchor long spans.
      groupLabel: month !== prevMonth ? cursor.slice(0, 4) : null,
      isWeekend: false,
    })
    prevMonth = month
    cursor = addDays(cursor, 7)
  }
  return cols
}

function monthColumns(start: string, end: string): AxisColumn[] {
  const cols: AxisColumn[] = []
  let [y, m] = monthStart(start).split('-').map(Number)
  const [ey, em] = monthStart(end).split('-').map(Number)
  let prevYear = -1
  while (y < ey || (y === ey && m <= em)) {
    const iso = `${String(y).padStart(4, '0')}-${String(m).padStart(2, '0')}-01`
    // Last day of the month: day 0 of the next month.
    const lastDom = new Date(Date.UTC(y, m, 0)).getUTCDate()
    cols.push({
      iso,
      endIso: `${iso.slice(0, 7)}-${String(lastDom).padStart(2, '0')}`,
      label: SHORT_MONTHS[m - 1],
      groupLabel: y !== prevYear ? String(y) : null,
      isWeekend: false,
    })
    prevYear = y
    m += 1
    if (m > 12) {
      m = 1
      y += 1
    }
  }
  return cols
}

/**
 * Build the column axis for a zoom level over the bars' date span. Returns null
 * when there are no bars (nothing to place). `columnOf` does a binary search over
 * the columns' day ranges, so placement is O(log cols) per bar.
 */
export function buildAxis(bars: GanttBar[], zoom: ZoomLevel): GanttAxis | null {
  if (bars.length === 0) return null
  const { start, end } = spanOf(bars)
  const columns =
    zoom === 'day'
      ? dayColumns(start, end)
      : zoom === 'week'
        ? weekColumns(start, end)
        : monthColumns(start, end)

  const columnOf = (iso: string): number => {
    // Clamp out-of-range dates to the edges so a due marker just past the span
    // still renders at the boundary rather than off-grid.
    if (iso <= columns[0].iso) return 0
    if (iso >= columns[columns.length - 1].iso) return columns.length - 1
    let lo = 0
    let hi = columns.length - 1
    while (lo <= hi) {
      const mid = (lo + hi) >> 1
      if (iso < columns[mid].iso) hi = mid - 1
      else if (iso > columns[mid].endIso) lo = mid + 1
      else return mid
    }
    return lo // between columns (shouldn't happen with contiguous ranges)
  }

  const today = todayISO()
  const todayIdx =
    today >= columns[0].iso && today <= columns[columns.length - 1].endIso
      ? columnOf(today)
      : -1

  return {
    columns,
    columnOf,
    todayIdx,
    daysPerColumn: NOMINAL_DAYS_PER_COLUMN[zoom],
  }
}
