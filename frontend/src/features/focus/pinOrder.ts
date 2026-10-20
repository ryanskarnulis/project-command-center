import type { ScheduledBlock } from '../../types/focus'

/*
 * The order of a day's floating pins (#375), shared by the desktop drag and the
 * mobile ⋯ sheet. The plan reports each floating pin's `pin_order`; first-fit
 * packing can put a later pin earlier on the clock, so this order, not the
 * start time, is what a reorder edits.
 */

/** The day's floating pins' entry ids, first to last. */
export function floatingPinOrder(scheduled: readonly ScheduledBlock[]): number[] {
  return scheduled
    .filter(
      (block): block is ScheduledBlock & { entry_id: number; pin_order: number } =>
        block.entry_id !== null && block.pin_order !== null && !block.fixed,
    )
    .sort((a, b) => a.pin_order - b.pin_order)
    .map((block) => block.entry_id)
}

/**
 * Move `entryId` by `delta` slots (-1 up, +1 down). Returns null when that is
 * no move (already at that end, a zero delta, or not in the order), so callers
 * can hide the verb or skip the write.
 */
export function shiftPin(order: readonly number[], entryId: number, delta: number): number[] | null {
  const from = order.indexOf(entryId)
  const to = from + delta
  if (from === -1 || delta === 0 || to < 0 || to >= order.length) return null
  const next = [...order]
  next.splice(from, 1)
  next.splice(to, 0, entryId)
  return next
}

/** Drop `draggedId` onto `targetId`'s slot, shifting the pins between them. */
export function dropPin(order: readonly number[], draggedId: number, targetId: number): number[] | null {
  const to = order.indexOf(targetId)
  return to === -1 ? null : shiftPin(order, draggedId, to - order.indexOf(draggedId))
}
