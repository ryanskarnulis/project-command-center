import { useState } from 'react'
import { useSettings } from './useSettings'
import type { Profile, ProfileUpdate, Prompt } from '../../types/settings'

const EVAL_SUITES = ['task_extraction', 'project_matching', 'summary'] as const

interface ActionState {
  busy: boolean
  error: string | null
}

function overridden(profile: Profile, field: string): boolean {
  return profile.overridden_fields.includes(field)
}

function ProfileEditor({
  profile,
  state,
  onSave,
}: {
  profile: Profile
  state: ActionState | undefined
  onSave: (name: string, fields: ProfileUpdate) => void
}) {
  const [model, setModel] = useState(profile.model)
  const [temperature, setTemperature] = useState(String(profile.temperature))
  const [maxTokens, setMaxTokens] = useState(String(profile.max_tokens))

  function handleSave() {
    const fields: ProfileUpdate = {}
    if (model !== profile.model) fields.model = model
    const tempNum = Number(temperature)
    if (!Number.isNaN(tempNum) && tempNum !== profile.temperature)
      fields.temperature = tempNum
    const mtNum = Number(maxTokens)
    if (!Number.isNaN(mtNum) && mtNum !== profile.max_tokens)
      fields.max_tokens = mtNum
    onSave(profile.name, fields)
  }

  return (
    <li className="settings-profile">
      <div className="settings-profile-header">
        <strong>{profile.name}</strong>
        <span className="settings-meta">
          {profile.provider} · {profile.response_mode} · {profile.system_prompt}
        </span>
      </div>

      <div className="settings-field">
        <label>
          Model {overridden(profile, 'model') && <em>(overridden)</em>}
          <input value={model} onChange={(e) => setModel(e.target.value)} />
        </label>
      </div>

      <div className="settings-field">
        <label>
          Temperature {overridden(profile, 'temperature') && <em>(overridden)</em>}
          <input
            type="number"
            step="0.1"
            min="0"
            max="2"
            value={temperature}
            onChange={(e) => setTemperature(e.target.value)}
          />
        </label>
        <label>
          Max tokens {overridden(profile, 'max_tokens') && <em>(overridden)</em>}
          <input
            type="number"
            min="1"
            max="8192"
            value={maxTokens}
            onChange={(e) => setMaxTokens(e.target.value)}
          />
        </label>
      </div>

      <div className="settings-actions">
        <button onClick={handleSave} disabled={state?.busy}>
          {state?.busy ? 'Saving…' : 'Save'}
        </button>
        {state?.error && <span className="error">{state.error}</span>}
      </div>
    </li>
  )
}

function PromptEditor({
  prompt,
  state,
  onSave,
}: {
  prompt: Prompt
  state: ActionState | undefined
  onSave: (name: string, text: string) => void
}) {
  const [text, setText] = useState(prompt.text)

  return (
    <li className="settings-prompt">
      <div className="settings-profile-header">
        <strong>{prompt.name}</strong>
      </div>
      <textarea
        className="settings-prompt-text"
        rows={10}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="settings-actions">
        <button onClick={() => onSave(prompt.name, text)} disabled={state?.busy}>
          {state?.busy ? 'Saving…' : 'Save'}
        </button>
        {state?.error && <span className="error">{state.error}</span>}
      </div>
    </li>
  )
}

export function SettingsPage() {
  const {
    profiles,
    prompts,
    loading,
    error,
    profileState,
    promptState,
    evalState,
    saveProfile,
    savePrompt,
    runEvals,
  } = useSettings()

  if (loading) return <p>Loading settings…</p>
  if (error) return <p className="error">Error: {error}</p>
  if (!profiles || !prompts) return null

  return (
    <div className="settings">
      <h1>Settings</h1>

      <section>
        <h2>Model profiles</h2>
        <p className="settings-note">
          Edits write to <code>profiles.local.yaml</code> (gitignored) and take effect
          without a restart. The committed <code>profiles.yaml</code> is never touched.
        </p>
        <ul className="settings-list">
          {profiles.map((profile) => (
            <ProfileEditor
              key={profile.name}
              profile={profile}
              state={profileState[profile.name]}
              onSave={saveProfile}
            />
          ))}
        </ul>
      </section>

      <section>
        <h2>Prompts</h2>
        <p className="settings-note">
          Saved straight to <code>ai/prompts/*.md</code>; the next model call reads the
          new text.
        </p>
        <ul className="settings-list">
          {prompts.map((prompt) => (
            <PromptEditor
              key={prompt.name}
              prompt={prompt}
              state={promptState[prompt.name]}
              onSave={savePrompt}
            />
          ))}
        </ul>
      </section>

      <section>
        <h2>Evals</h2>
        <p className="settings-note">Runs synchronously against Ollama.</p>
        <ul className="settings-list">
          {EVAL_SUITES.map((suite) => {
            const state = evalState[suite]
            return (
              <li key={suite} className="settings-eval">
                <div className="settings-actions">
                  <strong>{suite}</strong>
                  <button onClick={() => runEvals(suite)} disabled={state?.running}>
                    {state?.running ? 'Running…' : 'Run evals'}
                  </button>
                  {state?.result && (
                    <span className="settings-meta">
                      {state.result.passed}/{state.result.total} passed
                    </span>
                  )}
                  {state?.error && <span className="error">{state.error}</span>}
                </div>
                {state?.result && state.result.passed < state.result.total && (
                  <ul className="settings-eval-fails">
                    {state.result.cases
                      .filter((c) => !c.passed)
                      .map((c) => (
                        <li key={c.name}>
                          <code>{c.name}</code>: {c.reason}
                        </li>
                      ))}
                  </ul>
                )}
              </li>
            )
          })}
        </ul>
      </section>
    </div>
  )
}
