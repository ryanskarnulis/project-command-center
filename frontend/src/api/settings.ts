import { apiClient } from './client'
import type {
  EvalRunRecord,
  EvalRunResult,
  Profile,
  ProfileUpdate,
  Prompt,
} from '../types/settings'

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export async function getProfiles(): Promise<Profile[]> {
  const res = await apiClient('/api/settings/profiles')
  return (await res.json()) as Profile[]
}

export async function updateProfile(
  name: string,
  fields: ProfileUpdate,
): Promise<Profile> {
  const res = await apiClient(`/api/settings/profiles/${name}`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify(fields),
  })
  return (await res.json()) as Profile
}

export async function getPrompts(): Promise<Prompt[]> {
  const res = await apiClient('/api/settings/prompts')
  return (await res.json()) as Prompt[]
}

export async function getPrompt(name: string): Promise<Prompt> {
  const res = await apiClient(`/api/settings/prompts/${name}`)
  return (await res.json()) as Prompt
}

export async function putPrompt(name: string, text: string): Promise<Prompt> {
  const res = await apiClient(`/api/settings/prompts/${name}`, {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify({ text }),
  })
  return (await res.json()) as Prompt
}

export async function runEval(suite: string): Promise<EvalRunResult> {
  const res = await apiClient(`/api/settings/evals/${suite}/run`, {
    method: 'POST',
  })
  return (await res.json()) as EvalRunResult
}

export async function getEvalRuns(
  suite?: string,
  limit = 10,
): Promise<EvalRunRecord[]> {
  const params = new URLSearchParams()
  if (suite) params.set('suite', suite)
  params.set('limit', String(limit))
  const res = await apiClient(`/api/settings/evals/runs?${params.toString()}`)
  return (await res.json()) as EvalRunRecord[]
}
