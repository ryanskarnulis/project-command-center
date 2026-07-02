import { useState } from 'react'
import { Repeat } from 'lucide-react'
import type { RepeatInterval } from '../../../types/task'
import { formatRepeatInterval, parseRepeatInterval } from '../../../utils/recurrence'
import { ChipPopover } from './ChipPopover'

interface Props {
  value: RepeatInterval | null
  onChange: (next: RepeatInterval | null) => void
  disabled?: boolean
  disabledHint?: string
}

interface EditorProps {
  value: RepeatInterval | null
  onCommit: (next: RepeatInterval | null) => void
  close: () => void
}

/**
 * Mounted fresh each time the popover opens. Same parse rules as the old
 * repeat form field, but with an explicit Set commit — a blur-save field
 * inside a click-outside-closing popover would lose edits silently.
 */
function RepeatEditor({ value, onCommit, close }: EditorProps) {
  const [draft, setDraft] = useState(value ? formatRepeatInterval(value) : '')
  const [error, setError] = useState<string | null>(null)

  function commit() {
    const trimmed = draft.trim()
    if (trimmed === '') {
      close()
      if (value !== null) onCommit(null)
      return
    }
    const parsed = parseRepeatInterval(trimmed)
    if (parsed === null) {
      setError('Try "weekly", "every 2 weeks", or "every 3 months"')
      return
    }
    close()
    if (!value || value.unit !== parsed.unit || value.every !== parsed.every) {
      onCommit(parsed)
    }
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
        aria-label="Repeat"
        autoFocus
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value)
          setError(null)
        }}
        placeholder="weekly, every 2 months"
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

export function RepeatChip({ value, onChange, disabled, disabledHint }: Props) {
  const empty = value === null
  return (
    <ChipPopover
      chip={
        <>
          <Repeat size={12} aria-hidden="true" />
          {empty ? 'Repeat…' : formatRepeatInterval(value)}
        </>
      }
      chipClassName={`repeat-badge${empty ? ' chip-empty' : ''}`}
      label={empty ? 'Set repeat' : `Repeat: ${formatRepeatInterval(value)}`}
      disabled={disabled}
      disabledHint={disabledHint}
    >
      {(close) => <RepeatEditor value={value} onCommit={onChange} close={close} />}
    </ChipPopover>
  )
}
