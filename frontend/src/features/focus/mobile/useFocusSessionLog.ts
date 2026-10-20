import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { finishFocusBlock, getFocusSessionLog, removeFocusBlock } from '../../../api/focusSessions'
import type { FocusFinishedBlock } from '../../../types/focus'
import type { FinishedBlock } from './focusDay'
import { migrateLegacyFocusStorage, onTabVisible, readSession, sendWrite } from './sessionSync'

/* What the day has already cost.
 *
 * `get_focus_plan` ranks open work only (`exclude_done=True`), so a block
 * completed during the session leaves the next fetch entirely. The timeline
 * still owes the user its finished rows, the capacity bar its spent segment
 * and the arithmetic its start offset — all three come from here.
 *
 * One day at a time, keyed by the plan's date: opening tomorrow's plan starts
 * an empty log, and coming back to today's restores it. Held on the server
 * (`focus_sessions`, #374) so a reload or a second device sees the same day.
 * Writes are optimistic: the state changes at once and the request follows on
 * the shared session queue. One that can't reach the server is retried from
 * the outbox; one the server refuses re-reads the server's log. */

interface SessionLog {
  date: string
  finished: FinishedBlock[]
}

function toBlock(block: FocusFinishedBlock): FinishedBlock {
  return {
    taskId: block.task_id,
    title: block.title,
    minutes: block.minutes,
    plannedMinutes: block.planned_minutes,
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
  const [state, setState] = useState<SessionLog>({ date, finished: [] })
  // A read that started before a local write would paint the pre-write log
  // over the optimistic one; every write bumps this so such a read is dropped.
  const version = useRef(0)
  // The day on screen, for a read that resolves after the user moved on.
  const dateRef = useRef(date)
  useEffect(() => {
    dateRef.current = date
  }, [date])

  // Filtered during render rather than reset in an effect: a day switch must
  // not paint one frame of yesterday's receipts against today's plan.
  const finished = useMemo(() => (state.date === date ? state.finished : []), [state, date])

  const load = useCallback((day: string) => {
    const started = version.current
    void migrateLegacyFocusStorage()
    readSession(() => getFocusSessionLog(day))
      .then((log) => {
        if (version.current !== started || dateRef.current !== day) return
        setState({ date: day, finished: log.finished.map(toBlock) })
      })
      .catch(() => {
        /* Unreachable, or writes still unsent: keep what is shown. */
      })
  }, [])

  useEffect(() => load(date), [date, load])
  useEffect(() => onTabVisible(() => load(dateRef.current)), [load])

  const commit = useCallback(
    (next: (current: FinishedBlock[]) => FinishedBlock[], write: () => Promise<unknown>) => {
      version.current += 1
      setState((current) => ({ date, finished: next(current.date === date ? current.finished : []) }))
      sendWrite(write, () => load(date))
    },
    [date, load],
  )

  const record = useCallback(
    (block: FinishedBlock) =>
      commit(
        (current) => [...current.filter((x) => x.taskId !== block.taskId), block],
        () => finishFocusBlock(date, block.taskId, block.minutes, block.plannedMinutes),
      ),
    [commit, date],
  )

  const remove = useCallback(
    (taskId: number) =>
      commit(
        (current) => current.filter((block) => block.taskId !== taskId),
        () => removeFocusBlock(date, taskId),
      ),
    [commit, date],
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
