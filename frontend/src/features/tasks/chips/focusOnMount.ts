/**
 * Callback ref that focuses a chip-popover editor input when it mounts.
 * Module-level (stable identity), so React invokes it only on mount/unmount.
 *
 * `preventScroll` is load-bearing: the ref fires during React's commit, before
 * ChipPopover's layout effect anchors the portaled popover — at that instant
 * the fixed-position popover still sits at its static position at the end of
 * <body>, far below the viewport. Plain focus() makes mobile browsers scroll
 * the document to the bottom chasing the input (React's `autoFocus` has the
 * same flaw — it maps to a plain focus() call).
 */
export function focusOnMount(el: HTMLInputElement | null): void {
  el?.focus({ preventScroll: true })
}
