import { useEffect, useState } from 'react'

/** Past this many seconds the working block adds the cold-model-load note —
 * the run is synchronous, so a first run of the day can take a minute. Shared
 * by the desktop `PendingExchange` and the mobile working block. */
export const SLOW_RUN_AFTER_S = 5

/**
 * Whole seconds since `running` last became true (or since `since`, a
 * wall-clock origin, when given); 0 while it is false.
 *
 * `since` lets a working block that remounted mid-run keep counting from the
 * run's real start instead of restarting at 0:00 (#353).
 *
 * Computed from a wall-clock origin on every tick rather than by counting
 * ticks: a browser that backgrounds the tab throttles timers, and the clock has
 * to be honest about how long the run has actually taken when the tab comes
 * back — it is the only signal the user has before the reply lands.
 */
export function useElapsedSeconds(running: boolean, since: number | null = null): number {
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!running) return
    const origin = since ?? Date.now()
    // Deferred so the effect's own render settles first; the value is the
    // same origin the interval measures from.
    const arm = setTimeout(() => {
      setStartedAt(origin)
      setNow(Date.now())
    }, 0)
    const tick = setInterval(() => setNow(Date.now()), 1000)
    return () => {
      clearTimeout(arm)
      clearInterval(tick)
    }
  }, [running, since])

  if (!running || startedAt === null) return 0
  return Math.max(0, Math.floor((now - startedAt) / 1000))
}

/** `m:ss` for the working block's clock. */
export function formatElapsed(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${minutes}:${rest < 10 ? `0${rest}` : rest}`
}
