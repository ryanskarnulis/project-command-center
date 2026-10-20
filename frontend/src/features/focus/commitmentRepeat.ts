import type { CommitmentRepeatInterval } from '../../types/focus'
import type { RepeatUnit } from '../../types/task'
import { formatRepeatInterval } from '../../utils/recurrence'

/**
 * The cadences the add-commitment forms offer (#376). The API takes any task
 * cadence (`every` 1–12) plus a weekly weekday set (#427); the forms keep to
 * the five a person reaches for.
 */
export type CommitmentRepeat = 'once' | 'weekdays' | RepeatUnit

export const COMMITMENT_REPEATS: { value: CommitmentRepeat; label: string }[] = [
  { value: 'once', label: 'Once' },
  { value: 'day', label: 'Daily' },
  { value: 'weekdays', label: 'Weekdays' },
  { value: 'week', label: 'Weekly' },
  { value: 'month', label: 'Monthly' },
]

/** Monday to Friday, in the backend's Monday-0 numbering. */
const MON_TO_FRI = [0, 1, 2, 3, 4]
const WEEKDAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export function commitmentRepeatInterval(
  value: CommitmentRepeat,
): CommitmentRepeatInterval | null {
  if (value === 'once') return null
  if (value === 'weekdays') return { unit: 'week', every: 1, days: [...MON_TO_FRI] }
  return { unit: value, every: 1 }
}

/**
 * A commitment cadence as words: the task wording (`daily`, `every 2 weeks`),
 * or for a weekday set `weekdays` (Mon–Fri) / `weekly on Mon, Wed` /
 * `every 2 weeks on Tue, Thu`. Mirrors the backend's `format_repeat`.
 */
export function formatCommitmentRepeat(interval: CommitmentRepeatInterval): string {
  const days = interval.days
  if (!days || days.length === 0) return formatRepeatInterval(interval)
  const sorted = [...days].sort((a, b) => a - b)
  if (interval.every === 1 && sorted.join() === MON_TO_FRI.join()) return 'weekdays'
  const names = sorted.map((day) => WEEKDAY_NAMES[day]).join(', ')
  return interval.every === 1
    ? `weekly on ${names}`
    : `every ${interval.every} weeks on ${names}`
}
