export interface Profile {
  name: string
  provider: string
  model: string
  temperature: number
  max_tokens: number
  response_mode: string
  system_prompt: string
  overridden_fields: string[]
}

export interface ProfileUpdate {
  model?: string
  temperature?: number
  max_tokens?: number
}

export interface Prompt {
  name: string
  text: string
}

export interface EvalCaseResult {
  name: string
  passed: boolean
  reason: string
}

export interface EvalRunResult {
  suite: string
  passed: number
  total: number
  cases: EvalCaseResult[]
}
