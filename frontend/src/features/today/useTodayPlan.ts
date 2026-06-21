import { useCallback, useEffect, useState } from 'react'
import { getTodayPlan } from '../../api/today'
import { ApiError } from '../../api/client'
import type { TodayPlan } from '../../types/today'

// Default controls. These match the backend defaults but are sent explicitly so
// the displayed state and the request never drift.
export const DEFAULT_START_TIME = '09:00'
export const DEFAULT_AVAILABLE_MINUTES = 360

/** Local (not UTC) calendar date as YYYY-MM-DD, matching the rest of the app. */
function localToday(): string {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

interface UseTodayPlan {
  plan: TodayPlan | null
  loading: boolean
  error: string | null
  date: string
  startTime: string
  availableMinutes: number
  setDate: (date: string) => void
  setStartTime: (startTime: string) => void
  setAvailableMinutes: (minutes: number) => void
  refetch: () => void
}

export function useTodayPlan(): UseTodayPlan {
  const [date, setDate] = useState<string>(localToday)
  const [startTime, setStartTime] = useState<string>(DEFAULT_START_TIME)
  const [availableMinutes, setAvailableMinutes] = useState<number>(
    DEFAULT_AVAILABLE_MINUTES,
  )
  const [plan, setPlan] = useState<TodayPlan | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Bumped by refetch() to force a reload without changing the controls.
  const [reloadToken, setReloadToken] = useState(0)

  useEffect(() => {
    let active = true
    setLoading(true)
    getTodayPlan({ date, startTime, availableMinutes })
      .then((result) => {
        if (!active) return
        setPlan(result)
        setError(null)
      })
      .catch((err: unknown) => {
        if (!active) return
        const message =
          err instanceof ApiError
            ? (err.body as { detail?: string })?.detail ?? `Error ${err.status}`
            : err instanceof Error
              ? err.message
              : 'Failed to load the day plan'
        setError(message)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [date, startTime, availableMinutes, reloadToken])

  const refetch = useCallback(() => {
    setReloadToken((token) => token + 1)
  }, [])

  return {
    plan,
    loading,
    error,
    date,
    startTime,
    availableMinutes,
    setDate,
    setStartTime,
    setAvailableMinutes,
    refetch,
  }
}
