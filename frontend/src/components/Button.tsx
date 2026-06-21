import type { ButtonHTMLAttributes, ReactNode } from 'react'

type ButtonVariant = 'default' | 'primary' | 'ghost' | 'danger'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  children: ReactNode
}

/**
 * Typed wrapper over the globally-styled `<button>`. The base button styling is
 * already global CSS; this adds named variants (`.btn--*`) so callers express
 * intent instead of copying class strings. `type` defaults to `button` to avoid
 * accidental form submits.
 */
export function Button({
  variant = 'default',
  type = 'button',
  className,
  children,
  ...rest
}: ButtonProps) {
  const classes = [variant !== 'default' && `btn--${variant}`, className]
    .filter(Boolean)
    .join(' ')
  return (
    <button type={type} className={classes || undefined} {...rest}>
      {children}
    </button>
  )
}
