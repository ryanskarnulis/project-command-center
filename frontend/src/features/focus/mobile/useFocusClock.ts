import { useCallback, useMemo, useState, useSyncExternalStore } from 'react'
import type { RunState } from './focusDay'

/* The current block's clock.
 *
 * There is no server column for "this block started at": the plan is derived,
 * not stored. So the clock lives on the client, keyed by task and day, and
 * `elapsed` is always recomputed from wall-clock time rather than accumulated
 * by a ticking counter — a backgrounded tab, a sleeping phone or a reload all
 * come back with the right number. The interval below only forces a re-render;
 * it never advances the value.
 *
 * Persisted in localStorage rather than sessionStorage because a block you
 * started is still running when the tab is closed and reopened. */
const STORAGE_KEY = 'focus.clock'

interface ClockRecord {
  taskId: number
  /** Plan date, so yesterday's clock can't be adopted by today's block. */
  date: string
  state: Exclude<RunState, 'idle'>
  /** Epoch ms the current run leg began; meaningless while paused. */
  startedAt: number
  /** Milliseconds banked by earlier legs. */
  accumulatedMs: number
}

/** A clock value that can be handed back after an undo. */
export type ClockSnapshot = ClockRecord | null

function read(): ClockRecord | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const value: unknown = JSON.parse(raw)
    if (!value || typeof value !== 'object') return null
    const record = value as Partial<ClockRecord>
    if (typeof record.taskId !== 'number' || typeof record.date !== 'string') return null
    if (record.state !== 'running' && record.state !== 'paused') return null
    if (typeof record.startedAt !== 'number' || typeof record.accumulatedMs !== 'number') return null
    return record as ClockRecord
  } catch {
    return null
  }
}

function write(record: ClockRecord | null): void {
  try {
    if (record) localStorage.setItem(STORAGE_KEY, JSON.stringify(record))
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* The clock still runs for this render pass; only persistence is lost. */
  }
}

/* The wall clock, as an external store.
 *
 * `elapsed` is always recomputed from the record's `startedAt`, never
 * accumulated by a counter, so a backgrounded tab, a sleeping phone or a
 * reload all come back with the right number — the heartbeat below only moves
 * the sample. Bucketed to the tick so the snapshot is stable between beats,
 * and only subscribed while a block is actually running. 15s rather than the
 * spec's 60s: the readout is minute-granular, and a whole minute of lag at the
 * boundary is visible. */
const TICK_MS = 15_000

function subscribeToTick(notify: () => void): () => void {
  const timer = setInterval(notify, TICK_MS)
  return () => clearInterval(timer)
}

function noTick(): () => void {
  return () => {}
}

function tickSnapshot(): number {
  return Math.floor(Date.now() / TICK_MS) * TICK_MS
}

function elapsedMsOf(record: ClockRecord | null, now: number): number {
  if (!record) return 0
  return record.accumulatedMs + (record.state === 'running' ? Math.max(0, now - record.startedAt) : 0)
}

export interface FocusClock {
  run: RunState
  /** Minutes spent in the current block, floored — the readout is minute-granular. */
  elapsed: number
  start: () => void
  pause: () => void
  /** Clears the clock: the block ended, or its time has been banked. */
  reset: () => void
  snapshot: () => ClockSnapshot
  restore: (snapshot: ClockSnapshot) => void
}

/**
 * @param taskId The current block's task, or null when the day is clear.
 * @param date   The plan's date, so a stale clock is dropped rather than reused.
 */
export function useFocusClock(taskId: number | null, date: string): FocusClock {
  const [record, setRecord] = useState<ClockRecord | null>(read)

  // A clock belongs only to the block that started it: a record naming another
  // task simply doesn't apply here. Deliberately filtered rather than deleted —
  // between an undo and the refetch that follows it, `taskId` is still the next
  // block for a render or two, and wiping on sight would throw away the very
  // clock the undo is restoring.
  const live = record && record.taskId === taskId && record.date === date ? record : null

  const running = live?.state === 'running'
  const subscribe = useMemo(() => (running ? subscribeToTick : noTick), [running])
  const now = useSyncExternalStore(subscribe, tickSnapshot, () => 0)
  const elapsed = Math.floor(elapsedMsOf(live, now) / 60_000)

  const commit = useCallback((next: ClockRecord | null) => {
    setRecord(next)
    write(next)
  }, [])

  const start = useCallback(() => {
    if (taskId === null) return
    const now = Date.now()
    commit({
      taskId,
      date,
      state: 'running',
      startedAt: now,
      accumulatedMs: elapsedMsOf(live, now),
    })
  }, [commit, date, live, taskId])

  const pause = useCallback(() => {
    if (live?.state !== 'running') return
    const now = Date.now()
    commit({ ...live, state: 'paused', startedAt: now, accumulatedMs: elapsedMsOf(live, now) })
  }, [commit, live])

  const reset = useCallback(() => commit(null), [commit])

  return useMemo<FocusClock>(
    () => ({
      run: live?.state ?? 'idle',
      elapsed,
      start,
      pause,
      reset,
      snapshot: () => live,
      // Undo puts the running clock back, not just the row: a swipe taken by
      // accident should cost nothing, including the time already logged.
      restore: (value) => commit(value),
    }),
    [commit, elapsed, live, pause, reset, start],
  )
}
