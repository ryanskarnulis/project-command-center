import type { RepeatInterval, RepeatUnit } from '../types/task'

// Bounds mirror the backend RepeatInterval schema (every: 1-12).
const MIN_EVERY = 1
const MAX_EVERY = 12

const SHORTHAND: Record<string, RepeatInterval> = {
  daily: { unit: 'day', every: 1 },
  weekly: { unit: 'week', every: 1 },
  monthly: { unit: 'month', every: 1 },
}

const UNIT_WORDS: Record<string, RepeatUnit> = {
  day: 'day',
  days: 'day',
  week: 'week',
  weeks: 'week',
  month: 'month',
  months: 'month',
}

/**
 * Parse natural recurrence text into a {@link RepeatInterval}.
 *
 * Accepts `"daily"`, `"weekly"`, `"monthly"`, and the `"every N <unit>"` /
 * `"N <unit>"` / `"every <unit>"` family (e.g. `"every 2 weeks"`,
 * `"3 months"`). Returns `null` for empty or unrecognized input, or when `N`
 * falls outside the backend's 1-12 range. The caller distinguishes "clear
 * recurrence" (empty string) from "invalid" before calling this.
 */
export function parseRepeatInterval(text: string): RepeatInterval | null {
  const trimmed = text.trim().toLowerCase()
  if (trimmed === '') return null
  if (trimmed in SHORTHAND) return { ...SHORTHAND[trimmed] }

  const match = trimmed.match(
    /^(?:every\s+)?(\d+)?\s*(day|days|week|weeks|month|months)$/,
  )
  if (!match) return null

  const every = match[1] === undefined ? 1 : Number(match[1])
  const unit = UNIT_WORDS[match[2]]
  if (
    unit === undefined ||
    !Number.isInteger(every) ||
    every < MIN_EVERY ||
    every > MAX_EVERY
  ) {
    return null
  }
  return { unit, every }
}

const PLURAL: Record<RepeatUnit, string> = {
  day: 'days',
  week: 'weeks',
  month: 'months',
}

const SHORTHAND_LABEL: Record<RepeatUnit, string> = {
  day: 'daily',
  week: 'weekly',
  month: 'monthly',
}

/**
 * Render a {@link RepeatInterval} as natural text — the inverse of
 * {@link parseRepeatInterval}. `every: 1` collapses to the shorthand
 * (`daily`/`weekly`/`monthly`); otherwise `"every N <units>"`.
 */
export function formatRepeatInterval(interval: RepeatInterval): string {
  if (interval.every === 1) return SHORTHAND_LABEL[interval.unit]
  return `every ${interval.every} ${PLURAL[interval.unit]}`
}
