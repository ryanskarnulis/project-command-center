import { useCallback, useMemo, useState } from 'react'
import type { FinishedBlock } from './focusDay'

/* What the day has already cost.
 *
 * `get_focus_plan` ranks open work only (`exclude_done=True`), so a block
 * completed during the session leaves the next fetch entirely. The timeline
 * still owes the user its finished rows, the capacity bar its spent segment
 * and the arithmetic its start offset — all three come from here.
 *
 * One day at a time, keyed by the plan's date: opening tomorrow's plan starts
 * an empty log, and coming back to today's restores it. Held in localStorage
 * so a reload mid-afternoon doesn't reset the day to full capacity. */
const STORAGE_KEY = 'focus.session'

interface SessionLog {
  date: string
  finished: FinishedBlock[]
}

function read(date: string): FinishedBlock[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const value: unknown = JSON.parse(raw)
    const log = value as Partial<SessionLog> | null
    if (!log || log.date !== date || !Array.isArray(log.finished)) return []
    return log.finished.filter(
      (block): block is FinishedBlock =>
        !!block &&
        typeof block.taskId === 'number' &&
        typeof block.title === 'string' &&
        typeof block.minutes === 'number' &&
        typeof block.plannedMinutes === 'number',
    )
  } catch {
    return []
  }
}

function write(date: string, finished: FinishedBlock[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ date, finished } satisfies SessionLog))
  } catch {
    /* The timeline is correct for this session either way; only the reload survives. */
  }
}

export interface FocusSessionLog {
  finished: FinishedBlock[]
  /** Total minutes the finished blocks actually took. */
  spent: number
  record: (block: FinishedBlock) => void
  /** Takes a block back out — the undo path after a completion is reversed. */
  remove: (taskId: number) => void
}

export function useFocusSessionLog(date: string): FocusSessionLog {
  const [state, setState] = useState<SessionLog>(() => ({ date, finished: read(date) }))
  // Reading during render rather than in an effect: a day switch must not paint
  // one frame of yesterday's receipts against today's plan.
  const finished = state.date === date ? state.finished : read(date)

  const commit = useCallback(
    (next: (current: FinishedBlock[]) => FinishedBlock[]) => {
      setState((current) => {
        const base = current.date === date ? current.finished : read(date)
        const value = next(base)
        write(date, value)
        return { date, finished: value }
      })
    },
    [date],
  )

  const record = useCallback(
    (block: FinishedBlock) => commit((current) => [...current.filter((x) => x.taskId !== block.taskId), block]),
    [commit],
  )

  const remove = useCallback(
    (taskId: number) => commit((current) => current.filter((block) => block.taskId !== taskId)),
    [commit],
  )

  return useMemo(
    () => ({
      finished,
      spent: finished.reduce((total, block) => total + block.minutes, 0),
      record,
      remove,
    }),
    [finished, record, remove],
  )
}
