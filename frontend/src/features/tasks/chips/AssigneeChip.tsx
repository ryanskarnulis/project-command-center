import { useState } from 'react'
import { ChipPopover } from './ChipPopover'

interface Props {
  value: string | null
  onChange: (next: string | null) => void
}

interface EditorProps {
  value: string | null
  onCommit: (next: string | null) => void
  close: () => void
}

/** Mounted fresh each time the popover opens, so the draft starts current. */
function AssigneeEditor({ value, onCommit, close }: EditorProps) {
  const [draft, setDraft] = useState(value ?? '')

  function commit() {
    const next = draft.trim() || null
    close()
    if (next !== value) onCommit(next)
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
        aria-label="Assignee"
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="Unassigned"
      />
      <button type="submit">Set</button>
    </form>
  )
}

export function AssigneeChip({ value, onChange }: Props) {
  const empty = value === null
  return (
    <ChipPopover
      chip={empty ? 'Assign…' : `👤 ${value}`}
      chipClassName={`assignee-pill${empty ? ' chip-empty' : ''}`}
      label={empty ? 'Set assignee' : `Assignee: ${value}`}
    >
      {(close) => <AssigneeEditor value={value} onCommit={onChange} close={close} />}
    </ChipPopover>
  )
}
