import { useState } from 'react'

export interface ChipSearchOption {
  id: number
  label: string
}

interface Props {
  options: ChipSearchOption[]
  selectedId: number | null
  /** aria-label + placeholder for the filter input. */
  searchLabel: string
  /** When set, renders a clear row (e.g. "Unassigned", "None") that picks null. */
  clearLabel?: string
  onPick: (id: number | null) => void
}

/** Filter-as-you-type option list shared by the project and parent-task chips. */
export function ChipSearchList({
  options,
  selectedId,
  searchLabel,
  clearLabel,
  onPick,
}: Props) {
  const [query, setQuery] = useState('')
  const trimmed = query.trim().toLowerCase()
  const visible = trimmed
    ? options.filter((o) => o.label.toLowerCase().includes(trimmed))
    : options

  return (
    <div className="chip-search">
      <input
        aria-label={searchLabel}
        autoFocus
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={searchLabel}
      />
      <div className="chip-search-list">
        {clearLabel && (
          <button
            type="button"
            className="chip-search-item chip-search-clear"
            aria-current={selectedId === null ? 'true' : undefined}
            onClick={() => onPick(null)}
          >
            {clearLabel}
          </button>
        )}
        {visible.map((option) => (
          <button
            key={option.id}
            type="button"
            className="chip-search-item"
            aria-current={option.id === selectedId ? 'true' : undefined}
            onClick={() => onPick(option.id)}
          >
            {option.label}
          </button>
        ))}
        {visible.length === 0 && !clearLabel && (
          <p className="chip-search-empty">No matches</p>
        )}
      </div>
    </div>
  )
}
