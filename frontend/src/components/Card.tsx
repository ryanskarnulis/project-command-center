import type { ElementType, ReactNode } from 'react'

interface CardProps {
  /** Render as a different element/component (e.g. `Link`) while keeping the surface. */
  as?: ElementType
  className?: string
  children: ReactNode
  /** Pass-through for the rendered element: href, to, onClick, aria-*, role, etc. */
  [key: string]: unknown
}

/**
 * A neutral surface primitive: the bordered, padded container the app keeps
 * re-deriving (`.task-card`, `.project-card`, dropdown panels). Polymorphic via
 * `as` so a card can be a `div`, a `Link`, or a `button` without restyling.
 */
export function Card({ as: Tag = 'div', className, children, ...rest }: CardProps) {
  const classes = ['card', className].filter(Boolean).join(' ')
  return (
    <Tag className={classes} {...rest}>
      {children}
    </Tag>
  )
}
