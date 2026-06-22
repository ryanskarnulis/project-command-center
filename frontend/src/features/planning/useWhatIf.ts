import { useCallback, useMemo, useState } from 'react'
import { ApiError } from '../../api/client'
import { previewWhatIf } from '../../api/planning'
import { updateTask } from '../../api/tasks'
import { useToast } from '../../components/ToastContext'
import type {
  ProjectGantt,
  WhatIfOverride,
  WhatIfShift,
} from '../../types/planning'

// Slice 6: staged, unsaved schedule experiments. Dragging/resizing in what-if
// mode *stages* a placement change instead of persisting it; each stage re-runs
// the backend preview (the same `compute_shifts` the committed path uses — the
// scheduling math is Python, prime directive #1) and we overlay the returned
// dates onto the real tasks so the chart shows the hypothetical schedule.
// Commit fires the ordinary task PATCHes (which cascade for real); discard drops
// the staged state. No frontend date math: every previewed start comes from the
// backend.

interface UseWhatIf {
  /** Whether what-if mode is on (drags stage instead of persisting). */
  active: boolean
  /** Number of distinct tasks the user has staged a change for. */
  stagedCount: number
  /** A staged change is in flight (preview pending) — for a subtle busy state. */
  pending: boolean
  /** Enter what-if mode (starts from the real schedule, nothing staged). */
  enter: () => void
  /** Stage a new `scheduled_start` for a task and refresh the preview. */
  stageStart: (taskId: number, newStart: string) => Promise<void>
  /** Stage a new `estimated_minutes` for a task and refresh the preview. */
  stageEstimate: (taskId: number, newMinutes: number) => Promise<void>
  /** Persist every staged change via the task PATCH (each cascades), then exit. */
  commit: () => Promise<void>
  /** Drop all staged changes and leave what-if mode. */
  discard: () => void
  /** Overlay the staged overrides + previewed shifts onto the real payload. */
  applyPreview: (data: ProjectGantt) => ProjectGantt
}

/** Best-effort message from an unknown error, preferring the API `detail`. */
function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    return (err.body as { detail?: string })?.detail ?? `Error ${err.status}`
  }
  if (err instanceof Error) return err.message
  return fallback
}

/** Merge a field into the override for `taskId` (last write wins per field). */
function withOverride(
  overrides: Map<number, WhatIfOverride>,
  taskId: number,
  patch: Partial<WhatIfOverride>,
): Map<number, WhatIfOverride> {
  const next = new Map(overrides)
  const existing = next.get(taskId) ?? { task_id: taskId }
  next.set(taskId, { ...existing, ...patch })
  return next
}

/**
 * Own the what-if staging state for a project timeline: the staged overrides, the
 * previewed shifts the backend returns for them, and the commit/discard actions.
 * `projectId` scopes the preview + commit; `onCommitted` lets the page refetch the
 * (now-real) schedule afterward.
 */
export function useWhatIf(
  projectId: number,
  onCommitted: () => void,
): UseWhatIf {
  const [active, setActive] = useState(false)
  const [overrides, setOverrides] = useState<Map<number, WhatIfOverride>>(
    new Map(),
  )
  const [shifts, setShifts] = useState<Map<number, string>>(new Map())
  const [pending, setPending] = useState(false)
  const { notify } = useToast()

  const enter = useCallback(() => {
    setActive(true)
    setOverrides(new Map())
    setShifts(new Map())
  }, [])

  const discard = useCallback(() => {
    setActive(false)
    setOverrides(new Map())
    setShifts(new Map())
  }, [])

  // Re-run the backend preview for a freshly-merged override set. The returned
  // shifts replace the prior preview wholesale (they are recomputed from scratch
  // each time, so a removed/relaxed override correctly stops shifting downstream).
  const refreshPreview = useCallback(
    async (nextOverrides: Map<number, WhatIfOverride>) => {
      setPending(true)
      try {
        const result = await previewWhatIf(projectId, [...nextOverrides.values()])
        setShifts(
          new Map(result.shifts.map((s: WhatIfShift) => [s.task_id, s.scheduled_start])),
        )
      } catch (err: unknown) {
        notify('error', errorMessage(err, 'Failed to preview the change'))
      } finally {
        setPending(false)
      }
    },
    [projectId, notify],
  )

  const stageStart = useCallback(
    async (taskId: number, newStart: string) => {
      const next = withOverride(overrides, taskId, { scheduled_start: newStart })
      setOverrides(next)
      await refreshPreview(next)
    },
    [overrides, refreshPreview],
  )

  const stageEstimate = useCallback(
    async (taskId: number, newMinutes: number) => {
      const next = withOverride(overrides, taskId, {
        estimated_minutes: newMinutes,
      })
      setOverrides(next)
      await refreshPreview(next)
    },
    [overrides, refreshPreview],
  )

  const commit = useCallback(async () => {
    // Each staged override is persisted via the ordinary task PATCH — the same
    // call a real drag/resize makes, so the server cascade runs for real. We send
    // only the fields the user actually staged.
    try {
      for (const o of overrides.values()) {
        const patch: { scheduled_start?: string; estimated_minutes?: number } = {}
        if (o.scheduled_start != null) patch.scheduled_start = o.scheduled_start
        if (o.estimated_minutes != null)
          patch.estimated_minutes = o.estimated_minutes
        if (Object.keys(patch).length > 0) await updateTask(o.task_id, patch)
      }
      notify('success', 'Schedule changes applied')
      setActive(false)
      setOverrides(new Map())
      setShifts(new Map())
      onCommitted()
    } catch (err: unknown) {
      // A partial commit can leave the real schedule mid-way; refetch so the page
      // reflects whatever did persist, and keep what-if mode for the user to retry.
      notify('error', errorMessage(err, 'Failed to apply changes'))
      onCommitted()
    }
  }, [overrides, notify, onCommitted])

  const applyPreview = useCallback(
    (data: ProjectGantt): ProjectGantt => {
      if (overrides.size === 0 && shifts.size === 0) return data
      return {
        ...data,
        tasks: data.tasks.map((t) => {
          const override = overrides.get(t.id)
          const previewedStart = shifts.get(t.id)
          if (override === undefined && previewedStart === undefined) return t
          return {
            ...t,
            // A cascaded shift sets the start; a direct override may also set the
            // estimate (which has no shift entry — it changes span, not start).
            scheduled_start: previewedStart ?? t.scheduled_start,
            estimated_minutes:
              override?.estimated_minutes != null
                ? override.estimated_minutes
                : t.estimated_minutes,
          }
        }),
      }
    },
    [overrides, shifts],
  )

  const stagedCount = useMemo(() => overrides.size, [overrides])

  return {
    active,
    stagedCount,
    pending,
    enter,
    stageStart,
    stageEstimate,
    commit,
    discard,
    applyPreview,
  }
}
