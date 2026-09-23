import { useEffect, useState } from 'react'

/**
 * Whole seconds since `running` last became true; 0 while it is false.
 *
 * Computed from a wall-clock origin on every tick rather than by counting
 * ticks: a phone that backgrounds the tab throttles timers, and the clock has
 * to be honest about how long the run has actually taken when the tab comes
 * back — it is the only signal the user has before the reply lands.
 */
export function useElapsedSeconds(running: boolean): number {
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!running) return
    const origin = Date.now()
    // Deferred so the effect's own render settles first; the value is the
    // same origin the interval measures from.
    const arm = setTimeout(() => {
      setStartedAt(origin)
      setNow(origin)
    }, 0)
    const tick = setInterval(() => setNow(Date.now()), 1000)
    return () => {
      clearTimeout(arm)
      clearInterval(tick)
    }
  }, [running])

  if (!running || startedAt === null) return 0
  return Math.max(0, Math.floor((now - startedAt) / 1000))
}

/** `m:ss` for the working block's clock. */
export function formatElapsed(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${minutes}:${rest < 10 ? `0${rest}` : rest}`
}
