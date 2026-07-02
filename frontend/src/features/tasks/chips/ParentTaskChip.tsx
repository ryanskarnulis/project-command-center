import { ChipPopover } from './ChipPopover'
import { ChipSearchList } from './ChipSearchList'
import type { ChipSearchOption } from './ChipSearchList'

interface Props {
  value: number | null
  /** Candidate parents with own descendants already excluded (no cycles). */
  options: ChipSearchOption[]
  onChange: (id: number | null) => void
}

export function ParentTaskChip({ value, options, onChange }: Props) {
  const title = options.find((o) => o.id === value)?.label ?? null
  const empty = value === null
  return (
    <ChipPopover
      chip={empty ? 'Set parent' : `Sub of ${title ?? '…'}`}
      chipClassName={`parent-pill${empty ? ' chip-empty' : ''}`}
      label={empty ? 'Set parent task' : `Parent task: ${title ?? value}`}
    >
      {(close) => (
        <ChipSearchList
          options={options}
          selectedId={value}
          searchLabel="Search tasks"
          clearLabel="None"
          onPick={(id) => {
            close()
            if (id !== value) onChange(id)
          }}
        />
      )}
    </ChipPopover>
  )
}
