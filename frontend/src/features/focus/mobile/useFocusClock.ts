import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { clearFocusClock, getFocusClock, setFocusClock } from '../../../api/focusSessions'
import type { FocusClock as ServerClock } from '../../../types/focus'
import type { RunState } from './focusDay'
import { migrateLegacyFocusStorage, onTabVisible, readSession, sendWrite } from './sessionSync'

/* The current block's clock.
 *
 * Held on the server (`focus_sessions`, #374): one live clock, so a reload, a
 * closed tab or a second device all come back to the same running block.
 * `elapsed` is always recomputed from wall-clock time rather than accumulated
 * by a ticking counter — a backgrounded tab or a sleeping phone comes back with
 * the right number. The interval below only forces a re-render; it never
 * advances the value.
 *
 * The server never sees this device's clock. It reports the block's elapsed
 * time as of its response; we anchor that to our own `Date.now()` on arrival,
 * and every write sends elapsed time, not a timestamp, so two devices whose
 * clocks disagree still agree on the block. Writes are optimistic and queued
 * (`sessionSync`): one that can't reach the server is retried from the outbox,
 * and one the server refuses re-reads the server's clock. */

interface ClockRecord {
  taskId: number
  /** Plan date, so yesterday's clock can't be adopted by today's block. */
  date: string
  state: Exclude<RunState, 'idle'>
  /** Local epoch ms the current run leg began; meaningless while paused. */
  startedAt: number
  /** Milliseconds banked by earlier legs. */
  accumulatedMs: number
}

/** A clock value that can be handed back after an undo. */
export type ClockSnapshot = ClockRecord | null

function fromServer(clock: ServerClock | null, receivedAt: number): ClockRecord | null {
  if (!clock) return null
  return {
    taskId: clock.task_id,
    date: clock.plan_date,
    state: clock.state,
    startedAt: receivedAt,
    accumulatedMs: clock.elapsed_ms,
  }
}

/* The wall clock, as an external store.
 *
 * Bucketed to the tick so the snapshot is stable between beats, and only
 * subscribed while a block is actually running. 15s rather than the spec's
 * 60s: the readout is minute-granular, and a whole minute of lag at the
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

/**
 * The write for a running record. Elapsed is sampled when the request actually
 * leaves, not when it was queued: the server starts its leg at its own now, so
 * time spent waiting behind earlier writes would otherwise fall out of the block.
 */
function sendRunning(record: ClockRecord): () => Promise<ServerClock> {
  return () => setFocusClock(record.date, record.taskId, 'running', elapsedMsOf(record, Date.now()))
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
  const [record, setRecord] = useState<ClockRecord | null>(null)
  // Bumped by every local write: a read that a newer local change overtook
  // must not paint the older server state over it.
  const version = useRef(0)

  const load = useCallback(() => {
    const started = version.current
    void migrateLegacyFocusStorage()
    readSession(getFocusClock)
      .then((clock) => {
        if (version.current === started) setRecord(fromServer(clock, Date.now()))
      })
      .catch(() => {
        /* Unreachable, or writes still unsent: keep what is shown. */
      })
  }, [])

  useEffect(() => load(), [load])
  useEffect(() => onTabVisible(load), [load])

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

  /** Apply `next` here at once; send it behind any writes still queued. */
  const commit = useCallback(
    (next: ClockRecord | null, write: () => Promise<unknown>) => {
      version.current += 1
      setRecord(next)
      // The local record stays authoritative on this device; the server copy is
      // for the next load and the next device. A write that can't reach the
      // server waits in the outbox; only a refusal re-reads the server.
      sendWrite(write, load)
    },
    [load],
  )

  const start = useCallback(() => {
    if (taskId === null) return
    const now = Date.now()
    const running: ClockRecord = { taskId, date, state: 'running', startedAt: now, accumulatedMs: elapsedMsOf(live, now) }
    commit(running, sendRunning(running))
  }, [commit, date, live, taskId])

  const pause = useCallback(() => {
    if (live?.state !== 'running') return
    const now = Date.now()
    const paused: ClockRecord = { ...live, state: 'paused', startedAt: now, accumulatedMs: elapsedMsOf(live, now) }
    commit(paused, () => setFocusClock(paused.date, paused.taskId, 'paused', paused.accumulatedMs))
  }, [commit, live])

  const clear = useCallback(
    () => commit(null, async () => {
      // Only this block's clock: another device may have started the next one.
      if (taskId !== null) await clearFocusClock(date, taskId)
    }),
    [commit, date, taskId],
  )

  return useMemo<FocusClock>(
    () => ({
      run: live?.state ?? 'idle',
      elapsed,
      start,
      pause,
      reset: clear,
      snapshot: () => live,
      // Undo puts the running clock back, not just the row: a swipe taken by
      // accident should cost nothing, including the time already logged.
      restore: (value) => {
        if (!value) {
          clear()
          return
        }
        commit(
          value,
          value.state === 'running'
            ? sendRunning(value)
            : () => setFocusClock(value.date, value.taskId, 'paused', value.accumulatedMs),
        )
      },
    }),
    [clear, commit, elapsed, live, pause, start],
  )
}
