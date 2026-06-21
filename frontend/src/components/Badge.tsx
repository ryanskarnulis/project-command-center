import type { ReactNode } from 'react'

export type BadgeTone =
  | 'blue'
  | 'green'
  | 'orange'
  | 'red'
  | 'purple'
  | 'neutral'

interface BadgeProps {
  /** Color vocabulary shared with the existing `.status-pill.tone-*` palette. */
  tone?: BadgeTone
  /** Extra classes for callers that still need a bespoke pill (e.g. `due-*`). */
  className?: string
  children: ReactNode
}

/**
 * Small inline status/label pill. Wraps the shared `.badge` base styling so
 * features stop hand-writing pill className strings. The tone names map 1:1 onto
 * the pre-existing `.status-pill.tone-*` colors, so swapping a hand-rolled pill
 * for `<Badge tone="…">` is a lossless visual change.
 */
export function Badge({ tone = 'neutral', className, children }: BadgeProps) {
  const classes = ['badge', `tone-${tone}`, className]
    .filter(Boolean)
    .join(' ')
  return <span className={classes}>{children}</span>
}
