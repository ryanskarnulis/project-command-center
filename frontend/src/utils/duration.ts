const MINUTE = 1
const HOUR = 60
const DAY = 60 * 24
const WEEK = DAY * 7

type DurationUnit = 'minutes' | 'hours' | 'days' | 'weeks'

export const DURATION_UNITS: DurationUnit[] = ['minutes', 'hours', 'days', 'weeks']

const UNIT_MINUTES: Record<DurationUnit, number> = {
  minutes: MINUTE,
  hours: HOUR,
  days: DAY,
  weeks: WEEK,
}

/** Convert a value + unit pair to integer minutes. Returns null when value is empty/invalid. */
export function toMinutes(value: number, unit: DurationUnit): number {
  return Math.round(value * UNIT_MINUTES[unit])
}

/**
 * Split a stored minute value into the largest whole unit for prefilling inputs.
 * e.g. 120 → { value: 2, unit: 'hours' }
 */
export function splitDuration(minutes: number): { value: number; unit: DurationUnit } {
  if (minutes % WEEK === 0) return { value: minutes / WEEK, unit: 'weeks' }
  if (minutes % DAY === 0) return { value: minutes / DAY, unit: 'days' }
  if (minutes % HOUR === 0) return { value: minutes / HOUR, unit: 'hours' }
  return { value: minutes, unit: 'minutes' }
}

/**
 * Human label for a stored estimate.
 * e.g. 60 → "1 hour", 90 → "90 min", 10080 → "1 week"
 */
export function formatDuration(minutes: number | null): string {
  if (minutes === null) return ''
  if (minutes === 0) return '0m'
  const { value, unit } = splitDuration(minutes)
  const singular = unit.endsWith('s') ? unit.slice(0, -1) : unit
  return `${value} ${value === 1 ? singular : unit}`
}

const INPUT_UNITS: Record<string, DurationUnit> = {
  '': 'minutes',
  m: 'minutes',
  min: 'minutes',
  mins: 'minutes',
  minute: 'minutes',
  minutes: 'minutes',
  h: 'hours',
  hr: 'hours',
  hrs: 'hours',
  hour: 'hours',
  hours: 'hours',
  d: 'days',
  day: 'days',
  days: 'days',
  w: 'weeks',
  wk: 'weeks',
  wks: 'weeks',
  week: 'weeks',
  weeks: 'weeks',
}

/** Prefill text for the friendly estimate input. */
export function formatDurationInput(minutes: number | null): string {
  return formatDuration(minutes)
}

/**
 * Parse a friendly estimate like "30m", "2h", "1 day", or "none".
 * Returns undefined when the text is invalid, null when the estimate is cleared.
 */
export function parseDurationInput(input: string): number | null | undefined {
  const trimmed = input.trim().toLowerCase()
  if (trimmed === '' || trimmed === 'none' || trimmed === 'no estimate') return null
  const match = trimmed.match(/^(\d+(?:\.\d+)?)\s*([a-z]*)$/)
  if (!match) return undefined
  const value = Number(match[1])
  const unit = INPUT_UNITS[match[2]]
  if (!Number.isFinite(value) || value <= 0 || unit === undefined) return undefined
  return toMinutes(value, unit)
}
