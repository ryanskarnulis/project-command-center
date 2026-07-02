import type { Project } from '../../../types/project'
import { ChipPopover } from './ChipPopover'
import { ChipSearchList } from './ChipSearchList'

interface Props {
  value: number | null
  projects: Project[]
  onChange: (id: number | null) => void
  /** Only a candidate task may be left unassigned (backend rehomes on accept). */
  allowUnassigned?: boolean
}

export function ProjectChip({ value, projects, onChange, allowUnassigned }: Props) {
  const name = projects.find((p) => p.id === value)?.name ?? null
  const empty = value === null
  return (
    <ChipPopover
      chip={empty ? 'Set project' : name ?? '…'}
      chipClassName={`source-pill${empty ? ' chip-empty' : ''}`}
      label={empty ? 'Set project' : `Project: ${name ?? value}`}
    >
      {(close) => (
        <ChipSearchList
          options={projects.map((p) => ({ id: p.id, label: p.name }))}
          selectedId={value}
          searchLabel="Search projects"
          clearLabel={allowUnassigned ? 'Unassigned' : undefined}
          onPick={(id) => {
            close()
            if (id !== value) onChange(id)
          }}
        />
      )}
    </ChipPopover>
  )
}
