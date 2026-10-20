import { ApiError } from '../../../api/client'
import { finishFocusBlock, getFocusClock, setFocusClock } from '../../../api/focusSessions'

/* Plumbing shared by the session log and the clock hooks (#374).
 *
 * One queue for every session request, reads included. The hooks update their
 * own state optimistically and send the write behind it; queueing keeps the
 * writes in the order the user made them (start then pause must not land as
 * pause then start), and a read queued after a write sees it. A failed request
 * doesn't stall the queue. */
let tail: Promise<unknown> = Promise.resolve()

export function enqueue<T>(request: () => Promise<T>): Promise<T> {
  const run = tail.then(request)
  tail = run.catch(() => undefined)
  return run
}

/** True when the server answered and said no, so sending it again won't help. */
export function isRefusal(reason: unknown): boolean {
  return reason instanceof ApiError && reason.status < 500
}

/* The outbox: what happens when a write can't reach the server.
 *
 * A phone loses signal mid-session. The hooks have already applied the change
 * on screen, so a write that fails to *arrive* (network down, timeout, a 5xx)
 * stays in the outbox, in order, and is sent again: on the next write, the next
 * read, the browser's `online` event, and a backoff timer. Every write is
 * idempotent and a running clock samples its elapsed time when it is sent, so a
 * late delivery books the same block it would have booked on time.
 *
 * Until the outbox drains, a read is skipped rather than painted: the server is
 * behind this device, and its answer would undo what the user just did.
 *
 * A write the server *refuses* (4xx: the task was purged, the body is invalid)
 * is dropped and its hook re-reads the server, which is the truth then.
 *
 * The outbox is in memory only. Reloading the page while offline loses what
 * was still unsent (the plan can't load offline either); the server keeps the
 * last state it did receive. */
interface OutboxItem {
  send: () => Promise<unknown>
  onRefused: () => void
}

const outbox: OutboxItem[] = []
const RETRY_BASE_MS = 5_000
const RETRY_MAX_MS = 5 * 60_000
let retryDelay = RETRY_BASE_MS
let retryTimer: ReturnType<typeof setTimeout> | null = null

function scheduleRetry(): void {
  if (retryTimer !== null) return
  retryTimer = setTimeout(() => {
    retryTimer = null
    void enqueue(flush)
  }, retryDelay)
  retryDelay = Math.min(retryDelay * 2, RETRY_MAX_MS)
}

/** Sends the outbox in order; true once it is empty. Runs on the queue only. */
async function flush(): Promise<boolean> {
  while (outbox.length > 0) {
    const item = outbox[0]
    try {
      await item.send()
    } catch (reason) {
      if (!isRefusal(reason)) {
        scheduleRetry()
        return false
      }
      outbox.shift()
      item.onRefused()
      continue
    }
    outbox.shift()
  }
  retryDelay = RETRY_BASE_MS
  return true
}

/**
 * Send a write behind every write before it. A delivery failure keeps it for a
 * retry; `onRefused` runs only when the server answers no.
 */
export function sendWrite(send: () => Promise<unknown>, onRefused: () => void): void {
  outbox.push({ send, onRefused })
  void enqueue(flush)
}

/** Thrown in place of a read while the outbox still holds unsent writes. */
export class UnsentWritesError extends Error {
  constructor() {
    super('Focus session writes are still unsent')
  }
}

/** A read that sees every write before it, or rejects while any is unsent. */
export function readSession<T>(get: () => Promise<T>): Promise<T> {
  return enqueue(async () => {
    if (!(await flush())) throw new UnsentWritesError()
    return get()
  })
}

/** How many writes are still waiting for the network. */
export function unsentWriteCount(): number {
  return outbox.length
}

if (typeof window !== 'undefined') {
  window.addEventListener('online', () => {
    if (outbox.length > 0) void enqueue(flush)
  })
}

/* One-time move of the pre-#374 localStorage state onto the server.
 *
 * Before #374 the log and the clock lived in this browser only (`focus.session`,
 * `focus.clock`). The first load after the upgrade uploads them, then clears
 * the keys, so a day in progress during the deploy keeps its receipts and its
 * running block. A clock is only adopted when the server has none: one that
 * another device started since is newer than anything this browser remembers.
 * A key is kept when the server couldn't be reached, so the next load retries;
 * a refusal (the task has since been purged, say) is final. */
const LEGACY_SESSION_KEY = 'focus.session'
const LEGACY_CLOCK_KEY = 'focus.clock'

interface LegacyBlock {
  taskId: number
  minutes: number
  plannedMinutes: number
}

interface LegacyClock {
  taskId: number
  date: string
  state: 'running' | 'paused'
  startedAt: number
  accumulatedMs: number
}

function readKey(key: string): unknown {
  try {
    const raw = localStorage.getItem(key)
    return raw === null ? undefined : (JSON.parse(raw) as unknown)
  } catch {
    return null
  }
}

function dropKey(key: string): void {
  try {
    localStorage.removeItem(key)
  } catch {
    /* Storage is unavailable; there was nothing to migrate from it either. */
  }
}

function legacyLog(value: unknown): { date: string; finished: LegacyBlock[] } | null {
  if (!value || typeof value !== 'object') return null
  const log = value as { date?: unknown; finished?: unknown }
  if (typeof log.date !== 'string' || !Array.isArray(log.finished)) return null
  const finished = (log.finished as unknown[]).filter((block): block is LegacyBlock => {
    const b = block as Partial<LegacyBlock> | null
    return (
      !!b &&
      typeof b.taskId === 'number' &&
      typeof b.minutes === 'number' &&
      typeof b.plannedMinutes === 'number'
    )
  })
  return { date: log.date, finished }
}

function legacyClock(value: unknown): LegacyClock | null {
  if (!value || typeof value !== 'object') return null
  const clock = value as Partial<LegacyClock>
  if (typeof clock.taskId !== 'number' || typeof clock.date !== 'string') return null
  if (clock.state !== 'running' && clock.state !== 'paused') return null
  if (typeof clock.startedAt !== 'number' || typeof clock.accumulatedMs !== 'number') return null
  return clock as LegacyClock
}

async function migrate(): Promise<void> {
  const session = readKey(LEGACY_SESSION_KEY)
  if (session !== undefined) {
    let keep = false
    const log = legacyLog(session)
    const date = log?.date ?? ''
    for (const block of log?.finished ?? []) {
      const minutes = Math.max(0, Math.round(block.minutes))
      const planned = Math.max(0, Math.round(block.plannedMinutes))
      try {
        await finishFocusBlock(date, block.taskId, minutes, planned)
      } catch (reason) {
        if (!isRefusal(reason)) keep = true
      }
    }
    if (!keep) dropKey(LEGACY_SESSION_KEY)
  }

  const stored = readKey(LEGACY_CLOCK_KEY)
  if (stored !== undefined) {
    let keep = false
    const clock = legacyClock(stored)
    if (clock) {
      try {
        if ((await getFocusClock()) === null) {
          const elapsed =
            clock.accumulatedMs + (clock.state === 'running' ? Math.max(0, Date.now() - clock.startedAt) : 0)
          await setFocusClock(clock.date, clock.taskId, clock.state, elapsed)
        }
      } catch (reason) {
        if (!isRefusal(reason)) keep = true
      }
    }
    if (!keep) dropKey(LEGACY_CLOCK_KEY)
  }
}

let migration: Promise<void> | null = null

/** Runs the move once per page load, ahead of every other session request. */
export function migrateLegacyFocusStorage(): Promise<void> {
  migration ??= enqueue(migrate)
  return migration
}

/** Test seam: forget the per-load state so each test starts from a fresh page. */
export function resetFocusSessionSyncForTests(): void {
  migration = null
  tail = Promise.resolve()
  outbox.length = 0
  retryDelay = RETRY_BASE_MS
  if (retryTimer !== null) clearTimeout(retryTimer)
  retryTimer = null
}

/** Re-read on return to the tab: another device may have moved the session on. */
export function onTabVisible(listener: () => void): () => void {
  const handler = (): void => {
    if (document.visibilityState === 'visible') listener()
  }
  document.addEventListener('visibilitychange', handler)
  return () => document.removeEventListener('visibilitychange', handler)
}
