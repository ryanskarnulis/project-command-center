import type { TaskWorkflowStatus } from '../../../types/task'
import { WORKFLOW_STATUSES, workflowLabel } from '../taskMeta'
import { ChipPopover } from './ChipPopover'

interface Props {
  value: TaskWorkflowStatus
  onChange: (next: TaskWorkflowStatus) => void
  disabled?: boolean
  disabledHint?: string
  /** When provided (recurring, not done), renders a "Skip occurrence…" item. */
  onSkipOccurrence?: () => void
}

export function StatusChip({
  value,
  onChange,
  disabled,
  disabledHint,
  onSkipOccurrence,
}: Props) {
  return (
    <ChipPopover
      chip={workflowLabel(value)}
      chipClassName={`status-pill workflow-${value}`}
      label={`Status: ${workflowLabel(value)}`}
      disabled={disabled}
      disabledHint={disabledHint}
    >
      {(close) => (
        <div className="chip-menu">
          {WORKFLOW_STATUSES.map((status) => (
            <button
              key={status}
              type="button"
              className={`chip-menu-item status-pill workflow-${status}`}
              aria-current={status === value ? 'true' : undefined}
              onClick={() => {
                close()
                if (status !== value) onChange(status)
              }}
            >
              {workflowLabel(status)}
            </button>
          ))}
          {onSkipOccurrence && (
            <button
              type="button"
              className="chip-menu-action"
              onClick={() => {
                close()
                onSkipOccurrence()
              }}
            >
              Skip occurrence…
            </button>
          )}
        </div>
      )}
    </ChipPopover>
  )
}
