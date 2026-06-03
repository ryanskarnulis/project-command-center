// Fixed set of effort-estimate options. The DB stores whole minutes
// (tasks.estimated_minutes); the UI only ever offers / shows these labels.

export interface DurationOption {
  minutes: number
  label: string
}

const MINUTE = 1
const HOUR = 60
const DAY = 60 * 24
const WEEK = DAY * 7

export const DURATION_OPTIONS: DurationOption[] = [
  { minutes: 5 * MINUTE, label: '5 minutes' },
  { minutes: 15 * MINUTE, label: '15 minutes' },
  { minutes: 30 * MINUTE, label: '30 minutes' },
  { minutes: 1 * HOUR, label: '1 hour' },
  { minutes: 2 * HOUR, label: '2 hours' },
  { minutes: 4 * HOUR, label: '4 hours' },
  { minutes: 1 * DAY, label: '1 day' },
  { minutes: 3 * DAY, label: '3 days' },
  { minutes: 1 * WEEK, label: '1 week' },
  { minutes: 2 * WEEK, label: '2 weeks' },
  { minutes: 4 * WEEK, label: '1 month' },
]

const LABEL_BY_MINUTES = new Map(
  DURATION_OPTIONS.map((o) => [o.minutes, o.label]),
)

/**
 * Human label for a stored estimate. Known option values map to their label;
 * any other value falls back to a plain "N min" so old/odd data still renders.
 */
export function formatDuration(minutes: number | null): string {
  if (minutes === null) return ''
  return LABEL_BY_MINUTES.get(minutes) ?? `${minutes} min`
}
