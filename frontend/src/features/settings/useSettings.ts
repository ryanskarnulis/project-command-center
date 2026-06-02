import { useCallback, useEffect, useState } from 'react'
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
}

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

  const saveProfile = useCallback((name: string, fields: ProfileUpdate) => {
    setProfileState((prev) => ({ ...prev, [name]: { busy: true, error: null } }))
    updateProfile(name, fields)
      .then((updated) => {
        setProfiles((prev) =>
          prev ? prev.map((p) => (p.name === name ? updated : p)) : prev,
        )
        setProfileState((prev) => ({ ...prev, [name]: { busy: false, error: null } }))
      })
      .catch((err: unknown) => {
        setProfileState((prev) => ({
          ...prev,
          [name]: { busy: false, error: errMessage(err) },
        }))
      })
  }, [])

  const savePrompt = useCallback((name: string, text: string) => {
    setPromptState((prev) => ({ ...prev, [name]: { busy: true, error: null } }))
    putPrompt(name, text)
      .then((updated) => {
        setPrompts((prev) =>
          prev ? prev.map((p) => (p.name === name ? updated : p)) : prev,
        )
        setPromptState((prev) => ({ ...prev, [name]: { busy: false, error: null } }))
      })
      .catch((err: unknown) => {
        setPromptState((prev) => ({
          ...prev,
          [name]: { busy: false, error: errMessage(err) },
        }))
      })
  }, [])

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
