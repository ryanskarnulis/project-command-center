import { useEffect, useState } from 'react'
import type { RepeatInterval } from '../../types/task'
import { formatRepeatInterval, parseRepeatInterval } from '../../utils/recurrence'

interface Props {
  value: RepeatInterval | null
  /** Fired on blur with a parsed interval, or `null` when the field is cleared. */
  onChange: (next: RepeatInterval | null) => void
  /** Disabled (with a tooltip) until the task has a due date. */
  disabled?: boolean
}

/**
 * Natural-text recurrence input, mirroring the inline estimate field: the user
 * types `weekly`, `every 2 months`, etc., and on blur the text is parsed. A
 * recognized value is normalized back to its canonical label; unrecognized text
 * surfaces an inline error and is not saved. An empty field clears recurrence.
 */
export function RepeatIntervalInput({ value, onChange, disabled = false }: Props) {
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setDraft(value ? formatRepeatInterval(value) : '')
    setError(null)
  }, [value])

  function commit() {
    const trimmed = draft.trim()
    if (trimmed === '') {
      setError(null)
      if (value !== null) onChange(null)
      return
    }
    const parsed = parseRepeatInterval(trimmed)
    if (parsed === null) {
      setError('Try "weekly", "every 2 weeks", or "every 3 months"')
      return
    }
    setError(null)
    setDraft(formatRepeatInterval(parsed))
    if (!value || value.unit !== parsed.unit || value.every !== parsed.every) {
      onChange(parsed)
    }
  }

  return (
    <div className="repeat-input">
      <input
        aria-label="Repeat"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        disabled={disabled}
        placeholder="e.g. weekly, every 2 months"
        title={disabled ? 'Set a due date to enable recurrence' : undefined}
      />
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </div>
  )
}
