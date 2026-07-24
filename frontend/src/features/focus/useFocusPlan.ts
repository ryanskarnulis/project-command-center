import { useCallback, useEffect, useState } from 'react'
import { getFocusPlan } from '../../api/focus'
import { ApiError } from '../../api/client'
import type { FocusPlan } from '../../types/focus'

// Fallbacks when nothing is persisted. Start time is only a fallback for a
// broken clock value — the real default is "now" (see roundedNow).
export const DEFAULT_START_TIME = '09:00'
export const DEFAULT_AVAILABLE_MINUTES = 360
export const DEFAULT_END_OF_DAY = '17:00'

// The backend rejects capacity outside this range (422); clamp before sending.
const MIN_AVAILABLE_MINUTES = 15
const MAX_AVAILABLE_MINUTES = 1440

// Capacity is either a fixed duration or computed from start time until a
// chosen end-of-day time.
export type CapacityMode = 'minutes' | 'until_end'

// Capacity settings persist across visits; start time deliberately does not —
// it resets to "now" each visit.
const CAPACITY_MODE_KEY = 'focus.capacityMode'
const CAPACITY_MINUTES_KEY = 'focus.capacity'
const END_OF_DAY_KEY = 'focus.endOfDay'

/** Local (not UTC) calendar date as YYYY-MM-DD, matching the rest of the app. */
function localToday(): string {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

/** Current local time as HH:MM, rounded up to the next 5 minutes. */
export function roundedNow(): string {
  const now = new Date()
  const total = Math.min(now.getHours() * 60 + now.getMinutes() + 4, 23 * 60 + 59)
  const rounded = Math.min(Math.floor(total / 5) * 5, 23 * 60 + 55)
  const hours = Math.floor(rounded / 60)
  const minutes = rounded % 60
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`
}

function parseTime(value: string): number {
  const [hours, minutes] = value.split(':')
  return Number(hours) * 60 + Number(minutes)
}

function readStoredMode(): CapacityMode {
  return localStorage.getItem(CAPACITY_MODE_KEY) === 'until_end'
    ? 'until_end'
    : 'minutes'
}

function readStoredMinutes(): number {
  const stored = Number(localStorage.getItem(CAPACITY_MINUTES_KEY))
  return Number.isInteger(stored) &&
    stored >= MIN_AVAILABLE_MINUTES &&
    stored <= MAX_AVAILABLE_MINUTES
    ? stored
    : DEFAULT_AVAILABLE_MINUTES
}

function readStoredEndOfDay(): string {
  const stored = localStorage.getItem(END_OF_DAY_KEY)
  return stored && /^([01]\d|2[0-3]):[0-5]\d$/.test(stored)
    ? stored
    : DEFAULT_END_OF_DAY
}

interface UseFocusPlan {
  plan: FocusPlan | null
  loading: boolean
  error: string | null
  windowError: string | null
  date: string
  startTime: string
  /** The resolved capacity actually sent to the API, whatever the mode. */
  availableMinutes: number
  capacityMode: CapacityMode
  capacityMinutes: number
  endOfDay: string
  setDate: (date: string) => void
  setStartTime: (startTime: string) => void
  setCapacityMinutes: (minutes: number) => void
  setCapacityMode: (mode: CapacityMode) => void
  setEndOfDay: (endOfDay: string) => void
  refetch: () => void
}

export function useFocusPlan(): UseFocusPlan {
  const [date, setDate] = useState<string>(localToday)
  const [startTime, setStartTime] = useState<string>(roundedNow)
  const [capacityMode, setCapacityModeState] = useState<CapacityMode>(readStoredMode)
  const [capacityMinutes, setCapacityMinutesState] =
    useState<number>(readStoredMinutes)
  const [endOfDay, setEndOfDayState] = useState<string>(readStoredEndOfDay)
  const [plan, setPlan] = useState<FocusPlan | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadedKey, setLoadedKey] = useState<string | null>(null)
  // Bumped by refetch() to force a reload without changing the controls.
  const [reloadToken, setReloadToken] = useState(0)

  const endOfDayMinutes = parseTime(endOfDay) - parseTime(startTime)
  const windowError =
    capacityMode === 'until_end' && endOfDayMinutes <= 0
      ? 'End of day must be later than start time.'
      : null
  const availableMinutes =
    capacityMode === 'until_end'
      ? Math.min(Math.max(endOfDayMinutes, MIN_AVAILABLE_MINUTES), MAX_AVAILABLE_MINUTES)
      : capacityMinutes

  const requestKey = JSON.stringify([date, startTime, availableMinutes, reloadToken])

  useEffect(() => {
    if (windowError) return
    let active = true
    getFocusPlan({ date, startTime, availableMinutes })
      .then((result) => {
        if (!active) return
        setPlan(result)
        setError(null)
        setLoadedKey(requestKey)
      })
      .catch((err: unknown) => {
        if (!active) return
        const message =
          err instanceof ApiError
            ? (err.body as { detail?: string })?.detail ?? `Error ${err.status}`
            : err instanceof Error
              ? err.message
              : 'Failed to prepare the focus session'
        setError(message)
        setLoadedKey(requestKey)
      })
    return () => {
      active = false
    }
  }, [date, startTime, availableMinutes, requestKey, windowError])

  const setCapacityMinutes = useCallback((minutes: number) => {
    const next = Number.isInteger(minutes) && minutes > 0 ? minutes : DEFAULT_AVAILABLE_MINUTES
    setCapacityMinutesState(next)
    setCapacityModeState('minutes')
    localStorage.setItem(CAPACITY_MINUTES_KEY, String(next))
    localStorage.setItem(CAPACITY_MODE_KEY, 'minutes')
  }, [])

  const setCapacityMode = useCallback((mode: CapacityMode) => {
    setCapacityModeState(mode)
    localStorage.setItem(CAPACITY_MODE_KEY, mode)
  }, [])

  const setEndOfDay = useCallback((value: string) => {
    const next = /^([01]\d|2[0-3]):[0-5]\d$/.test(value) ? value : DEFAULT_END_OF_DAY
    setEndOfDayState(next)
    localStorage.setItem(END_OF_DAY_KEY, next)
  }, [])

  const refetch = useCallback(() => {
    setReloadToken((token) => token + 1)
  }, [])

  return {
    plan,
    loading: loadedKey !== requestKey,
    error,
    windowError,
    date,
    startTime,
    availableMinutes,
    capacityMode,
    capacityMinutes,
    endOfDay,
    setDate,
    setStartTime,
    setCapacityMinutes,
    setCapacityMode,
    setEndOfDay,
    refetch,
  }
}
