import { useSyncExternalStore } from 'react'
import type { ConversationDetail } from '../../types/agent'

/** One conversation's run while its `send` is in flight (#353). */
export interface InFlightRun {
  /** What the user sent — the optimistic bubble's text. */
  text: string
  /** Wall-clock start, so a remounted working block's clock keeps counting
   * from the real start instead of restarting at 0:00. */
  startedAt: number
  /** Newest message id the thread held when the send started (0 for an
   * empty thread, null if it was not loaded). The server commits the user
   * turn before the run, so a thread fetched mid-run already holds it — any
   * user message past this id is that turn, and the optimistic bubble would
   * repeat it. */
  afterId: number | null
  /** A Stop was requested and the run has not landed yet (#324). */
  stopping: boolean
  /** Settles once the run has landed and its sender refetched the thread —
   * with that fresh thread, or null when the refetch failed. A hook instance
   * that did not start the run (it remounted mid-run) waits on this to show
   * the reply. Never rejects. */
  landed: Promise<ConversationDetail | null>
}

/*
 * The in-flight run lives outside the component tree for the same reason as
 * `deleteUndoStore`: leaving the page mid-run (any top-level route change, or
 * `/agent/:id → /agent`, which remounts `AgentPage` through `RequireRouteId`)
 * unmounts the hook that started it. Its `send` continuation keeps running but
 * writes into a dead instance, so the remounted page saw neither the working
 * block nor its Stop, and never learned when the reply landed. Keyed by
 * conversation id, so a run keeps belonging to its own thread. The store holds
 * data only — the mounted hook does the fetching and state writes.
 */
const runs = new Map<number, InFlightRun>()
const listeners = new Set<() => void>()

function emit(): void {
  for (const listener of listeners) listener()
}

/** Record a run as started. Returns `land`, which the sender calls exactly
 * once with its post-run refetch (or null): waiters on `landed` see the fresh
 * thread first, and only then does the run leave the store — so a remounted
 * page swaps the working block for the reply instead of flashing the
 * pre-reply thread in between. `land` resolves once the run is cleared. */
export function startRun(
  conversationId: number,
  text: string,
  afterId: number | null,
): (fresh: ConversationDetail | null) => Promise<void> {
  let resolveLanded: (fresh: ConversationDetail | null) => void = () => {}
  const landed = new Promise<ConversationDetail | null>((resolve) => {
    resolveLanded = resolve
  })
  const run: InFlightRun = { text, startedAt: Date.now(), afterId, stopping: false, landed }
  runs.set(conversationId, run)
  emit()
  return async (fresh) => {
    resolveLanded(fresh)
    // Callbacks already chained on `landed` run before this continuation.
    await landed
    // A newer send on the same thread replaced this entry; leave it alone.
    if (runs.get(conversationId)?.landed !== landed) return
    runs.delete(conversationId)
    emit()
  }
}

/** Mark (or unmark) a Stop on the conversation's in-flight run, if any. */
export function setRunStopping(conversationId: number, stopping: boolean): void {
  const run = runs.get(conversationId)
  if (run === undefined || run.stopping === stopping) return
  runs.set(conversationId, { ...run, stopping })
  emit()
}

/** Drop every run — test isolation only. */
export function resetInFlightRuns(): void {
  if (runs.size === 0) return
  runs.clear()
  emit()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useInFlightRun(conversationId: number | null): InFlightRun | null {
  return useSyncExternalStore(
    subscribe,
    () => (conversationId === null ? null : (runs.get(conversationId) ?? null)),
    () => null,
  )
}
