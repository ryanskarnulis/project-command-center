import type { TaskPriority } from '../../../types/task'
import { PRIORITIES } from '../taskMeta'
import { ChipPopover } from './ChipPopover'

interface Props {
  value: TaskPriority
  onChange: (next: TaskPriority) => void
  disabled?: boolean
  disabledHint?: string
}

export function PriorityChip({ value, onChange, disabled, disabledHint }: Props) {
  return (
    <ChipPopover
      chip={value}
      chipClassName={`priority-pill priority-${value}`}
      label={`Priority: ${value}`}
      disabled={disabled}
      disabledHint={disabledHint}
    >
      {(close) => (
        <div className="chip-menu">
          {PRIORITIES.map((priority) => (
            <button
              key={priority}
              type="button"
              className={`chip-menu-item priority-pill priority-${priority}`}
              aria-current={priority === value ? 'true' : undefined}
              onClick={() => {
                close()
                if (priority !== value) onChange(priority)
              }}
            >
              {priority}
            </button>
          ))}
        </div>
      )}
    </ChipPopover>
  )
}
