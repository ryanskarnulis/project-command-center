import type { Task } from '../../types/task'
import { formatDuration, formatDurationShort } from '../../utils/duration'

/** How the clocked time landed against the plan. */
export type EstimateVerdict = 'under' | 'equal' | 'over'

export interface EstimateVsActual {
  /** "est 30m" */
  estimateLabel: string
  /** "took 45m" */
  actualLabel: string
  verdict: EstimateVerdict
  /** Spelled-out sentence for the hover title and screen readers. */
  description: string
}

/**
 * The done-archive comparison of a task's estimate against the time a timed
 * Focus block actually clocked (#347), or null when there is nothing to compare.
 *
 * Both numbers must be present: an untimed completion stores a null
 * `actual_minutes` on purpose, and reading that as "took 0m" would be a lie.
 * Only done tasks qualify — a reopened task can still carry the actual from its
 * last completion, and an open row is no place for a retrospective.
 */
export function estimateVsActual(
  task: Pick<Task, 'workflow_status' | 'estimated_minutes' | 'actual_minutes'>,
): EstimateVsActual | null {
  const { estimated_minutes: estimate, actual_minutes: actual } = task
  if (task.workflow_status !== 'done' || estimate === null || actual === null) {
    return null
  }
  const verdict: EstimateVerdict =
    actual > estimate ? 'over' : actual < estimate ? 'under' : 'equal'
  const diff = Math.abs(actual - estimate)
  const description =
    verdict === 'equal'
      ? `Took ${formatDuration(actual)}, exactly as estimated`
      : `Took ${formatDuration(actual)}, ${formatDuration(diff)} ${
          verdict === 'over' ? 'over' : 'under'
        } the ${formatDuration(estimate)} estimate`
  return {
    estimateLabel: `est ${formatDurationShort(estimate)}`,
    actualLabel: `took ${formatDurationShort(actual)}`,
    verdict,
    description,
  }
}
