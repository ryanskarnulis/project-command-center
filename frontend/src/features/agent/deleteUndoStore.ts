import { useSyncExternalStore } from 'react'

/** Undo lifetime for a conversation delete — M05i's five seconds. */
export const UNDO_LIFETIME_MS = 5000

export interface PendingDeleteUndo {
  id: number
  title: string | null
  /** Deleting the active conversation navigated to `/agent`; undo goes back. */
  wasActive: boolean
}

/*
 * The pending undo lives outside the component tree on purpose. Deleting the
 * active conversation navigates to `/agent`, and `/agent` and
 * `/agent/:conversationId` are separate route entries (the latter wrapped in
 * `RequireRouteId`), so the page remounts and any state armed a moment before
 * would be thrown away with it — the bar never painted (found by the browser
 * verifier, not by jsdom). A tiny external store, subscribed through
 * `useSyncExternalStore` exactly like `useMobileViewport`, is the smallest
 * thing that survives the remount. The store holds only *data*: the mounted
 * page performs the restore and the navigation with its own hooks, because a
 * `navigate` captured by an unmounted instance is a silent no-op.
 */
let pending: PendingDeleteUndo | null = null
let timer: ReturnType<typeof setTimeout> | null = null
const listeners = new Set<() => void>()

function emit(): void {
  for (const listener of listeners) listener()
}

function clearTimer(): void {
  if (timer !== null) clearTimeout(timer)
  timer = null
}

export function armDeleteUndo(next: PendingDeleteUndo): void {
  clearTimer()
  pending = next
  timer = setTimeout(() => {
    pending = null
    timer = null
    emit()
  }, UNDO_LIFETIME_MS)
  emit()
}

export function dismissDeleteUndo(): void {
  clearTimer()
  if (pending === null) return
  pending = null
  emit()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useDeleteUndo(): PendingDeleteUndo | null {
  return useSyncExternalStore(subscribe, () => pending, () => null)
}
