/**
 * dataTransfer type carrying a project id for manual reordering. Both the
 * sidebar list and the dashboard swimlane board speak it; the shared
 * `PUT /api/projects/order` call keeps the two surfaces in sync.
 */
export const PROJECT_DRAG_TYPE = 'application/x-pcc-project'

export function isProjectDrag(e: { dataTransfer: DataTransfer }): boolean {
  return e.dataTransfer.types.includes(PROJECT_DRAG_TYPE)
}

/** Move `draggedId` to `targetId`'s position, shifting the rest. */
export function moveBefore<T>(
  items: readonly T[],
  idOf: (item: T) => number,
  draggedId: number,
  targetId: number,
): T[] {
  const from = items.findIndex((item) => idOf(item) === draggedId)
  const to = items.findIndex((item) => idOf(item) === targetId)
  // Same-reference return lets setState callers bail out of a re-render.
  if (from === -1 || to === -1 || from === to) return items as T[]
  const next = [...items]
  const [dragged] = next.splice(from, 1)
  next.splice(to, 0, dragged)
  return next
}
