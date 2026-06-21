import type { ReactNode } from 'react'

interface AsyncStateProps {
  loading: boolean
  error: string | null
  /** Whether the loaded data set is empty (drives the empty-state message). */
  isEmpty?: boolean
  loadingLabel?: ReactNode
  emptyLabel?: ReactNode
  /** The loaded content. Rendered alongside a loading line (stale-while-revalidate). */
  children?: ReactNode
}

/**
 * One shared loading / error / empty baseline so pages stop hand-rolling the same
 * three branches. Status lines are additive around `children`: a loading line can
 * show above already-loaded content during a refetch; the empty message only shows
 * once loading and error are clear.
 */
export function AsyncState({
  loading,
  error,
  isEmpty = false,
  loadingLabel = 'Loading…',
  emptyLabel = 'Nothing here yet.',
  children,
}: AsyncStateProps) {
  return (
    <>
      {loading && <p className="async-loading">{loadingLabel}</p>}
      {error && (
        <p role="alert" className="async-error">
          {error}
        </p>
      )}
      {children}
      {!loading && !error && isEmpty && (
        <p className="async-empty">{emptyLabel}</p>
      )}
    </>
  )
}
