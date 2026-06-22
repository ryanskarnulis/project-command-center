import { useCallback, useEffect, useMemo, useState } from 'react'
import { useBlocker } from 'react-router-dom'
import {
  Check,
  ClipboardCheck,
  MessageSquare,
  RefreshCw,
  SlidersHorizontal,
  Wifi,
  WifiOff,
} from 'lucide-react'
import { Modal } from '../../components/Modal'
import { useSettings } from './useSettings'
import type {
  EvalRunRecord,
  OllamaStatus,
  Profile,
  ProfileUpdate,
  Prompt,
} from '../../types/settings'

const EVAL_SUITES = ['task_extraction', 'project_matching', 'summary'] as const

type Section = 'profiles' | 'prompts' | 'evals'

interface ActionState {
  busy: boolean
  error: string | null
  saved: boolean
}

function overridden(profile: Profile, field: string): boolean {
  return profile.overridden_fields.includes(field)
}

// The set of fields whose edited values differ from the loaded profile. Shared
// by the dirty check and the save handler so the two never disagree.
function profileChanges(
  profile: Profile,
  model: string,
  temperature: string,
  maxTokens: string,
): ProfileUpdate {
  const fields: ProfileUpdate = {}
  if (model !== profile.model) fields.model = model
  const tempNum = Number(temperature)
  if (!Number.isNaN(tempNum) && tempNum !== profile.temperature)
    fields.temperature = tempNum
  const mtNum = Number(maxTokens)
  if (!Number.isNaN(mtNum) && mtNum !== profile.max_tokens) fields.max_tokens = mtNum
  return fields
}

// Marks an overridden field with a tag + an inline per-field reset that reverts
// just that field to its committed profiles.yaml value. Renders nothing when the
// field isn't overridden.
function OverrideTag({
  profile,
  field,
  busy,
  onReset,
}: {
  profile: Profile
  field: string
  busy: boolean
  onReset: (name: string, field?: string) => void
}) {
  if (!overridden(profile, field)) return null
  return (
    <span className="settings-override">
      <em>(overridden)</em>
      <button
        type="button"
        className="settings-reset-field"
        onClick={() => onReset(profile.name, field)}
        disabled={busy}
        title="Reset this field to its committed default"
      >
        reset
      </button>
    </span>
  )
}

function UnsavedDot() {
  return <span className="settings-dirty-dot" aria-label="Unsaved changes" title="Unsaved changes" />
}

function SavedConfirmation() {
  return (
    <span className="settings-saved" role="status">
      <Check size={14} aria-hidden="true" />
      Saved
    </span>
  )
}

// Sentinel <option> value that switches the model picker to the free-text input,
// so a not-yet-pulled or custom model name is still enterable.
const CUSTOM_MODEL = '__custom__'

function ModelField({
  profile,
  model,
  models,
  busy,
  onChange,
  onReset,
}: {
  profile: Profile
  model: string
  models: string[]
  busy: boolean
  onChange: (value: string) => void
  onReset: (name: string, field?: string) => void
}) {
  // The current value is always selectable even if it isn't installed (e.g. not
  // yet pulled), so the dropdown never silently re-defaults the model.
  const options = models.includes(model) ? models : [model, ...models]
  // Default to the dropdown; "Custom…" is the opt-in escape to a free-text name.
  const [custom, setCustom] = useState(false)

  return (
    <div className="settings-field">
      <label>
        Model{' '}
        <OverrideTag profile={profile} field="model" busy={busy} onReset={onReset} />
        {custom ? (
          <input
            value={model}
            onChange={(e) => onChange(e.target.value)}
            placeholder="model name (e.g. gemma4:e2b)"
          />
        ) : (
          <select
            value={model}
            onChange={(e) => {
              if (e.target.value === CUSTOM_MODEL) setCustom(true)
              else onChange(e.target.value)
            }}
          >
            {options.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
            <option value={CUSTOM_MODEL}>Custom…</option>
          </select>
        )}
      </label>
      {custom && (
        <button
          type="button"
          className="secondary-action settings-model-pick"
          onClick={() => setCustom(false)}
        >
          Choose from list
        </button>
      )}
    </div>
  )
}

function ProfileEditor({
  profile,
  models,
  state,
  onSave,
  onReset,
  onDirtyChange,
}: {
  profile: Profile
  models: string[]
  state: ActionState | undefined
  onSave: (name: string, fields: ProfileUpdate) => void
  onReset: (name: string, field?: string) => void
  onDirtyChange: (name: string, dirty: boolean) => void
}) {
  const [model, setModel] = useState(profile.model)
  const [temperature, setTemperature] = useState(String(profile.temperature))
  const [maxTokens, setMaxTokens] = useState(String(profile.max_tokens))

  const dirty =
    Object.keys(profileChanges(profile, model, temperature, maxTokens)).length > 0

  useEffect(() => {
    onDirtyChange(profile.name, dirty)
    return () => onDirtyChange(profile.name, false)
  }, [profile.name, dirty, onDirtyChange])

  function handleSave() {
    onSave(profile.name, profileChanges(profile, model, temperature, maxTokens))
  }

  return (
    <li className="settings-profile">
      <div className="settings-profile-header">
        <strong>
          {profile.name}
          {dirty && <UnsavedDot />}
        </strong>
        <span className="settings-meta">
          {profile.provider} · {profile.response_mode} · {profile.system_prompt}
        </span>
      </div>

      <ModelField
        profile={profile}
        model={model}
        models={models}
        busy={!!state?.busy}
        onChange={setModel}
        onReset={onReset}
      />

      <div className="settings-field">
        <label>
          Temperature{' '}
          <OverrideTag
            profile={profile}
            field="temperature"
            busy={!!state?.busy}
            onReset={onReset}
          />
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
          Max tokens{' '}
          <OverrideTag
            profile={profile}
            field="max_tokens"
            busy={!!state?.busy}
            onReset={onReset}
          />
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
        <button onClick={handleSave} disabled={state?.busy || !dirty}>
          {state?.busy ? 'Saving…' : 'Save'}
        </button>
        <button
          type="button"
          className="secondary-action"
          onClick={() => onReset(profile.name)}
          disabled={state?.busy || profile.overridden_fields.length === 0}
          title="Reset all overridden fields to their committed defaults"
        >
          Reset to default
        </button>
        {state?.saved && <SavedConfirmation />}
        {state?.error && <span className="error">{state.error}</span>}
      </div>
    </li>
  )
}

function PromptEditor({
  prompt,
  workflows,
  state,
  onSave,
  onDirtyChange,
}: {
  prompt: Prompt
  workflows: string[]
  state: ActionState | undefined
  onSave: (name: string, text: string) => void
  onDirtyChange: (name: string, dirty: boolean) => void
}) {
  const [text, setText] = useState(prompt.text)

  const dirty = text !== prompt.text

  useEffect(() => {
    onDirtyChange(prompt.name, dirty)
    return () => onDirtyChange(prompt.name, false)
  }, [prompt.name, dirty, onDirtyChange])

  return (
    <li className="settings-prompt">
      <div className="settings-profile-header">
        <strong>
          {prompt.name}
          {dirty && <UnsavedDot />}
        </strong>
        <span className="settings-prompt-tags">
          {workflows.length > 0 ? (
            workflows.map((wf) => (
              <span key={wf} className="settings-prompt-tag">
                {wf}
              </span>
            ))
          ) : (
            <span className="settings-meta">unused</span>
          )}
        </span>
      </div>
      <textarea
        className="settings-prompt-text"
        rows={14}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="settings-actions">
        <button onClick={() => onSave(prompt.name, text)} disabled={state?.busy || !dirty}>
          {state?.busy ? 'Saving…' : 'Save'}
        </button>
        <button
          type="button"
          className="secondary-action"
          onClick={() => setText(prompt.text)}
          disabled={state?.busy || !dirty}
        >
          Revert
        </button>
        <span className="settings-char-count">{text.length} chars</span>
        {state?.saved && <SavedConfirmation />}
        {state?.error && <span className="error">{state.error}</span>}
      </div>
    </li>
  )
}

function passRate(run: EvalRunRecord): number {
  return run.total > 0 ? run.passed / run.total : 0
}

// Compact pass-rate sparkline across a suite's recent runs. Records arrive
// newest-first; reverse so the bars read oldest→newest left-to-right and the
// rightmost bar is the latest run.
function EvalTrend({ runs }: { runs: EvalRunRecord[] }) {
  if (runs.length === 0) return <span className="settings-meta">No runs yet</span>

  const ordered = [...runs].reverse()
  const latest = runs[0]
  // Compare the latest run against the immediately previous one for the same
  // suite (runs arrive newest-first): a drop flags a prompt/profile edit that
  // hurt this suite, so it can be caught before it lands in the corpus.
  const previous = runs[1]
  const regressed = previous !== undefined && passRate(latest) < passRate(previous)

  return (
    <div className="eval-trend">
      <div className="eval-trend-bars" role="img" aria-label={`${runs.length} recent runs`}>
        {ordered.map((run) => {
          const rate = passRate(run)
          return (
            <span
              key={run.id}
              className={`eval-trend-bar${rate === 1 ? ' is-full' : ''}`}
              style={{ height: `${Math.max(rate * 100, 6)}%` }}
              title={`${run.passed}/${run.total} · ${new Date(run.created_at).toLocaleString()}`}
            />
          )
        })}
      </div>
      <span className="eval-trend-summary">
        {Math.round(passRate(latest) * 100)}% · {runs.length} run{runs.length === 1 ? '' : 's'}
      </span>
      {regressed && (
        <span
          className="status-pill tone-red"
          title={`Down from ${Math.round(passRate(previous) * 100)}% on the previous run`}
        >
          ▼ regressed
        </span>
      )}
    </div>
  )
}

function HealthPanel({
  status,
  modelCount,
  checking,
  onRecheck,
}: {
  status: OllamaStatus | null
  modelCount: number
  checking: boolean
  onRecheck: () => void
}) {
  const reachable = status?.reachable ?? false
  return (
    <div className={`settings-health${reachable ? ' is-up' : ' is-down'}`}>
      <span className="settings-health-state">
        {reachable ? (
          <Wifi size={16} aria-hidden="true" />
        ) : (
          <WifiOff size={16} aria-hidden="true" />
        )}
        {checking ? 'Checking…' : reachable ? 'Ollama connected' : 'Ollama not reachable'}
      </span>
      {status?.host && <code className="settings-health-host">{status.host}</code>}
      {reachable && (
        <span className="settings-meta">
          {modelCount} model{modelCount === 1 ? '' : 's'} installed
        </span>
      )}
      <button
        type="button"
        className="secondary-action settings-health-recheck"
        onClick={onRecheck}
        disabled={checking}
      >
        <RefreshCw size={14} aria-hidden="true" />
        Re-check
      </button>
    </div>
  )
}

export function SettingsPage() {
  const {
    profiles,
    prompts,
    loading,
    error,
    ollamaStatus,
    ollamaChecking,
    models,
    recheckOllama,
    profileState,
    promptState,
    evalState,
    evalRuns,
    saveProfile,
    resetProfile,
    savePrompt,
    runEvals,
    runAllEvals,
  } = useSettings()

  const [section, setSection] = useState<Section>('profiles')

  // Which workflow profiles consume each prompt, derived from the loaded
  // profiles' system_prompt (which is the prompt's filename). Frontend-only join.
  const promptWorkflows = useMemo(() => {
    const map: Record<string, string[]> = {}
    for (const profile of profiles ?? [])
      (map[profile.system_prompt] ??= []).push(profile.name)
    return map
  }, [profiles])

  // Dirty flags reported up from each editor, keyed by `${kind}:${name}`.
  // Editors clear their entry on unmount, so switching tabs (which unmounts the
  // hidden section's editors and discards their edits) keeps this accurate.
  const [dirtyMap, setDirtyMap] = useState<Record<string, boolean>>({})

  const reportDirty = useCallback((kind: string, name: string, dirty: boolean) => {
    setDirtyMap((prev) => {
      const key = `${kind}:${name}`
      if ((prev[key] ?? false) === dirty) return prev
      return { ...prev, [key]: dirty }
    })
  }, [])

  const onProfileDirty = useCallback(
    (name: string, dirty: boolean) => reportDirty('profile', name, dirty),
    [reportDirty],
  )
  const onPromptDirty = useCallback(
    (name: string, dirty: boolean) => reportDirty('prompt', name, dirty),
    [reportDirty],
  )

  const anyDirty = Object.values(dirtyMap).some(Boolean)
  const anyEvalRunning = Object.values(evalState).some((s) => s.running)

  useEffect(() => {
    if (!anyDirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [anyDirty])

  const blocker = useBlocker(anyDirty)
  const navigationBlocked = blocker.state === 'blocked'

  function stayOnSettings() {
    if (blocker.state === 'blocked') blocker.reset()
  }

  function leaveSettings() {
    if (blocker.state === 'blocked') blocker.proceed()
  }

  if (loading) return <div className="page-loading">Loading settings…</div>
  if (error) return <p role="alert" className="error">Error: {error}</p>
  if (!profiles || !prompts) return null

  const tabs: { id: Section; label: string; icon: typeof SlidersHorizontal; count: number }[] = [
    { id: 'profiles', label: 'Profiles', icon: SlidersHorizontal, count: profiles.length },
    { id: 'prompts', label: 'Prompts', icon: MessageSquare, count: prompts.length },
    { id: 'evals', label: 'Evals', icon: ClipboardCheck, count: EVAL_SUITES.length },
  ]

  return (
    <main className="settings">
      <div className="section-heading">
        <h1>Settings</h1>
      </div>
      <p className="settings-note">
        Tune the AI subsystem: model profiles, prompts, and evals. Edits take effect
        without a restart and never touch the committed defaults.
      </p>

      <HealthPanel
        status={ollamaStatus}
        modelCount={models.length}
        checking={ollamaChecking}
        onRecheck={recheckOllama}
      />

      <nav className="settings-nav" aria-label="Settings sections">
        {tabs.map(({ id, label, icon: Icon, count }) => (
          <button
            key={id}
            type="button"
            className={`settings-nav-tab${section === id ? ' is-active' : ''}`}
            aria-current={section === id ? 'page' : undefined}
            onClick={() => setSection(id)}
          >
            <Icon size={16} aria-hidden="true" />
            {label}
            <span className="nav-count-badge" aria-hidden="true">{count}</span>
          </button>
        ))}
      </nav>

      {section === 'profiles' && (
        <section>
          <h2 className="settings-section-title">
            <SlidersHorizontal size={18} aria-hidden="true" />
            Model profiles
          </h2>
          <p className="settings-note">
            Edits write to <code>profiles.local.yaml</code> (gitignored) and take effect
            without a restart. The committed <code>profiles.yaml</code> is never touched.
          </p>
          <ul className="settings-list">
            {profiles.map((profile) => (
              <ProfileEditor
                // Include the override signature so a save/reset that changes the
                // committed-vs-effective values remounts the editor, re-seeding its
                // inputs from the new profile instead of stale local state.
                key={`${profile.name}:${profile.overridden_fields.join(',')}`}
                profile={profile}
                models={models}
                state={profileState[profile.name]}
                onSave={saveProfile}
                onReset={resetProfile}
                onDirtyChange={onProfileDirty}
              />
            ))}
          </ul>
        </section>
      )}

      {section === 'prompts' && (
        <section>
          <h2 className="settings-section-title">
            <MessageSquare size={18} aria-hidden="true" />
            Prompts
          </h2>
          <p className="settings-note">
            Saved straight to <code>ai/prompts/*.md</code>; the next model call reads the
            new text.
          </p>
          <ul className="settings-list">
            {prompts.map((prompt) => (
              <PromptEditor
                key={prompt.name}
                prompt={prompt}
                workflows={promptWorkflows[prompt.name] ?? []}
                state={promptState[prompt.name]}
                onSave={savePrompt}
                onDirtyChange={onPromptDirty}
              />
            ))}
          </ul>
        </section>
      )}

      {section === 'evals' && (
        <section>
          <div className="settings-section-head">
            <h2 className="settings-section-title">
              <ClipboardCheck size={18} aria-hidden="true" />
              Evals
            </h2>
            <button
              type="button"
              className="settings-run-all"
              onClick={() => runAllEvals([...EVAL_SUITES])}
              disabled={anyEvalRunning}
            >
              {anyEvalRunning ? 'Running…' : 'Run all suites'}
            </button>
          </div>
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
                  <EvalTrend runs={evalRuns[suite] ?? []} />
                </li>
              )
            })}
          </ul>
        </section>
      )}

      <Modal
        open={navigationBlocked}
        title="Discard unsaved settings?"
        onClose={stayOnSettings}
      >
        <p>
          You have unsaved profile or prompt edits. Leaving Settings will discard
          those changes.
        </p>
        <div className="modal-actions">
          <button type="button" className="secondary-action" onClick={stayOnSettings}>
            Stay
          </button>
          <button type="button" className="danger-action" onClick={leaveSettings}>
            Leave without saving
          </button>
        </div>
      </Modal>
    </main>
  )
}
