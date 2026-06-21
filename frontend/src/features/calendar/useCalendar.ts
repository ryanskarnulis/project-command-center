import { useCallback, useEffect, useMemo, useState } from 'react'
import { getCalendar } from '../../api/calendar'
import { ApiError } from '../../api/client'
import type { Task } from '../../types/task'

export type CalendarView = 'month' | 'week'

/** Local (not UTC) calendar date as YYYY-MM-DD, matching the rest of the app. */
export function toLocalISO(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function startOfWeek(date: Date): Date {
  const d = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  d.setDate(d.getDate() - d.getDay()) // back up to Sunday
  return d
}

function addDays(date: Date, days: number): Date {
  const d = new Date(date)
  d.setDate(d.getDate() + days)
  return d
}

/**
 * The inclusive [start, end] grid range for the anchor + view. Month spills into
 * adjacent weeks so the grid is always full weeks; week is the Sun–Sat around the
 * anchor. Returned as Date objects so the page can lay out cells without reparsing.
 */
export function gridRange(anchor: Date, view: CalendarView): { start: Date; end: Date } {
  if (view === 'week') {
    const start = startOfWeek(anchor)
    return { start, end: addDays(start, 6) }
  }
  const firstOfMonth = new Date(anchor.getFullYear(), anchor.getMonth(), 1)
  const lastOfMonth = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0)
  const start = startOfWeek(firstOfMonth)
  // Pad the tail to a full Sun–Sat week.
  const end = addDays(startOfWeek(lastOfMonth), 6)
  return { start, end }
}

interface UseCalendar {
  tasks: Task[]
  loading: boolean
  error: string | null
  anchor: Date
  view: CalendarView
  range: { start: Date; end: Date }
  setView: (view: CalendarView) => void
  goToPrev: () => void
  goToNext: () => void
  goToToday: () => void
}

export function useCalendar(): UseCalendar {
  const [anchor, setAnchor] = useState<Date>(() => new Date())
  const [view, setView] = useState<CalendarView>('month')
  const [tasks, setTasks] = useState<Task[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loadedKey, setLoadedKey] = useState<string | null>(null)

  const range = useMemo(() => gridRange(anchor, view), [anchor, view])
  const start = toLocalISO(range.start)
  const end = toLocalISO(range.end)
  const requestKey = `${start}|${end}`

  useEffect(() => {
    let active = true
    getCalendar({ start, end })
      .then((result) => {
        if (!active) return
        setTasks(result)
        setError(null)
        setLoadedKey(requestKey)
      })
      .catch((err: unknown) => {
        if (!active) return
        const message =
          err instanceof ApiError
            ? ((err.body as { detail?: string })?.detail ?? `Error ${err.status}`)
            : err instanceof Error
              ? err.message
              : 'Failed to load the calendar'
        setError(message)
        setLoadedKey(requestKey)
      })
    return () => {
      active = false
    }
  }, [start, end, requestKey])

  const step = useCallback(
    (direction: 1 | -1) => {
      setAnchor((current) => {
        if (view === 'week') return addDays(current, 7 * direction)
        return new Date(current.getFullYear(), current.getMonth() + direction, 1)
      })
    },
    [view],
  )

  const goToPrev = useCallback(() => step(-1), [step])
  const goToNext = useCallback(() => step(1), [step])
  const goToToday = useCallback(() => setAnchor(new Date()), [])

  return {
    tasks,
    loading: loadedKey !== requestKey,
    error,
    anchor,
    view,
    range,
    setView,
    goToPrev,
    goToNext,
    goToToday,
  }
}
