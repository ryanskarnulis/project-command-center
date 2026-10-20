import type { RepeatInterval, RepeatUnit } from '../../types/task'

/**
 * The cadences the add-commitment forms offer (#376). The API takes any task
 * cadence (`every` 1–12); the forms keep to the four a person reaches for.
 */
export type CommitmentRepeat = 'once' | RepeatUnit

export const COMMITMENT_REPEATS: { value: CommitmentRepeat; label: string }[] = [
  { value: 'once', label: 'Once' },
  { value: 'day', label: 'Daily' },
  { value: 'week', label: 'Weekly' },
  { value: 'month', label: 'Monthly' },
]

export function commitmentRepeatInterval(value: CommitmentRepeat): RepeatInterval | null {
  return value === 'once' ? null : { unit: value, every: 1 }
}
