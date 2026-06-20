import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getEvalRuns,
  getProfiles,
  getPrompts,
  putPrompt,
  runEval,
  updateProfile,
} from '../../api/settings'
import type {
  EvalRunRecord,
  EvalRunResult,
  Profile,
  ProfileUpdate,
  Prompt,
} from '../../types/settings'
import { ApiError } from '../../api/client'

function errMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const detail = (err.body as { detail?: string } | null)?.detail
    return detail ?? `Error ${err.status}`
  }
  if (err instanceof Error) return err.message
  return 'Unknown error'
}

interface ActionState {
  busy: boolean
  error: string | null
  saved: boolean
}

// How long the transient "Saved ✓" confirmation stays up before auto-clearing.
const SAVED_CLEAR_MS = 3000

interface EvalState {
  running: boolean
  result: EvalRunResult | null
  error: string | null
}

interface UseSettings {
  profiles: Profile[] | null
  prompts: Prompt[] | null
  loading: boolean
  error: string | null
  profileState: Record<string, ActionState>
  promptState: Record<string, ActionState>
  evalState: Record<string, EvalState>
  evalRuns: Record<string, EvalRunRecord[]>
  saveProfile: (name: string, fields: ProfileUpdate) => void
  savePrompt: (name: string, text: string) => void
  runEvals: (suite: string) => void
}

export function useSettings(): UseSettings {
  const [profiles, setProfiles] = useState<Profile[] | null>(null)
  const [prompts, setPrompts] = useState<Prompt[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [profileState, setProfileState] = useState<Record<string, ActionState>>({})
  const [promptState, setPromptState] = useState<Record<string, ActionState>>({})
  const [evalState, setEvalState] = useState<Record<string, EvalState>>({})
  const [evalRuns, setEvalRuns] = useState<Record<string, EvalRunRecord[]>>({})

  // Per-item timers that auto-clear the "Saved ✓" confirmation. Keyed by
  // `${kind}:${name}` so a profile and prompt of the same name don't collide.
  const savedTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({})

  const scheduleSavedClear = useCallback(
    (key: string, clear: () => void) => {
      const existing = savedTimers.current[key]
      if (existing) clearTimeout(existing)
      savedTimers.current[key] = setTimeout(() => {
        delete savedTimers.current[key]
        clear()
      }, SAVED_CLEAR_MS)
    },
    [],
  )

  useEffect(() => {
    const timers = savedTimers.current
    return () => {
      for (const id of Object.values(timers)) clearTimeout(id)
    }
  }, [])

  useEffect(() => {
    Promise.all([getProfiles(), getPrompts()])
      .then(([p, pr]) => {
        setProfiles(p)
        setPrompts(pr)
      })
      .catch((err: unknown) => setError(errMessage(err)))
      .finally(() => setLoading(false))
  }, [])

  // Eval-run history is non-critical: load it, but never block the page on it.
  const refreshRuns = useCallback((suite: string) => {
    getEvalRuns(suite)
      .then((runs) => setEvalRuns((prev) => ({ ...prev, [suite]: runs })))
      .catch(() => {
        /* history is best-effort; the run result itself still shows */
      })
  }, [])

  useEffect(() => {
    getEvalRuns(undefined, 50)
      .then((runs) => {
        const grouped: Record<string, EvalRunRecord[]> = {}
        for (const run of runs) (grouped[run.suite] ??= []).push(run)
        setEvalRuns(grouped)
      })
      .catch(() => {
        /* history is best-effort */
      })
  }, [])

  const saveProfile = useCallback(
    (name: string, fields: ProfileUpdate) => {
      setProfileState((prev) => ({
        ...prev,
        [name]: { busy: true, error: null, saved: false },
      }))
      updateProfile(name, fields)
        .then((updated) => {
          setProfiles((prev) =>
            prev ? prev.map((p) => (p.name === name ? updated : p)) : prev,
          )
          setProfileState((prev) => ({
            ...prev,
            [name]: { busy: false, error: null, saved: true },
          }))
          scheduleSavedClear(`profile:${name}`, () =>
            setProfileState((prev) =>
              prev[name]?.saved
                ? { ...prev, [name]: { ...prev[name], saved: false } }
                : prev,
            ),
          )
        })
        .catch((err: unknown) => {
          setProfileState((prev) => ({
            ...prev,
            [name]: { busy: false, error: errMessage(err), saved: false },
          }))
        })
    },
    [scheduleSavedClear],
  )

  const savePrompt = useCallback(
    (name: string, text: string) => {
      setPromptState((prev) => ({
        ...prev,
        [name]: { busy: true, error: null, saved: false },
      }))
      putPrompt(name, text)
        .then((updated) => {
          setPrompts((prev) =>
            prev ? prev.map((p) => (p.name === name ? updated : p)) : prev,
          )
          setPromptState((prev) => ({
            ...prev,
            [name]: { busy: false, error: null, saved: true },
          }))
          scheduleSavedClear(`prompt:${name}`, () =>
            setPromptState((prev) =>
              prev[name]?.saved
                ? { ...prev, [name]: { ...prev[name], saved: false } }
                : prev,
            ),
          )
        })
        .catch((err: unknown) => {
          setPromptState((prev) => ({
            ...prev,
            [name]: { busy: false, error: errMessage(err), saved: false },
          }))
        })
    },
    [scheduleSavedClear],
  )

  const runEvals = useCallback((suite: string) => {
    setEvalState((prev) => ({
      ...prev,
      [suite]: { running: true, result: null, error: null },
    }))
    runEval(suite)
      .then((result) => {
        setEvalState((prev) => ({
          ...prev,
          [suite]: { running: false, result, error: null },
        }))
        refreshRuns(suite)
      })
      .catch((err: unknown) => {
        setEvalState((prev) => ({
          ...prev,
          [suite]: { running: false, result: null, error: errMessage(err) },
        }))
      })
  }, [refreshRuns])

  return {
    profiles,
    prompts,
    loading,
    error,
    profileState,
    promptState,
    evalState,
    evalRuns,
    saveProfile,
    savePrompt,
    runEvals,
  }
}
