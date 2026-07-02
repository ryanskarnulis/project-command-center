import { useState } from 'react'
import { formatDuration, formatDurationInput, parseDurationInput } from '../../../utils/duration'
import { ChipPopover } from './ChipPopover'

interface Props {
  value: number | null
  onChange: (minutes: number | null) => void
  disabled?: boolean
  disabledHint?: string
}

interface EditorProps {
  value: number | null
  onCommit: (minutes: number | null) => void
  close: () => void
}

/** Mounted fresh each time the popover opens, so the draft starts current. */
function EstimateEditor({ value, onCommit, close }: EditorProps) {
  const [draft, setDraft] = useState(formatDurationInput(value))
  const [error, setError] = useState<string | null>(null)

  function commit() {
    const trimmed = draft.trim()
    if (trimmed === '') {
      close()
      if (value !== null) onCommit(null)
      return
    }
    const parsed = parseDurationInput(trimmed)
    if (parsed === undefined) {
      setError('Use something like 30m, 2h, or 1 day')
      return
    }
    close()
    if (parsed !== value) onCommit(parsed)
  }

  return (
    <form
      className="chip-editor"
      onSubmit={(e) => {
        e.preventDefault()
        commit()
      }}
    >
      <input
        aria-label="Estimate"
        autoFocus
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value)
          setError(null)
        }}
        placeholder="30m, 2h, 1 day"
      />
      <button type="submit">Set</button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </form>
  )
}

export function EstimateChip({ value, onChange, disabled, disabledHint }: Props) {
  const empty = value === null
  return (
    <ChipPopover
      chip={empty ? 'Estimate…' : `~${formatDuration(value)}`}
      chipClassName={`estimate${empty ? ' chip-empty' : ''}`}
      label={empty ? 'Set estimate' : `Estimate: ${formatDuration(value)}`}
      disabled={disabled}
      disabledHint={disabledHint}
    >
      {(close) => <EstimateEditor value={value} onCommit={onChange} close={close} />}
    </ChipPopover>
  )
}
