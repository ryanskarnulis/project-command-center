import type { TaskWorkflowStatus } from '../../../types/task'
import { WORKFLOW_STATUSES, workflowLabel } from '../taskMeta'
import { ChipPopover } from './ChipPopover'

interface Props {
  value: TaskWorkflowStatus
  onChange: (next: TaskWorkflowStatus) => void
  disabled?: boolean
  disabledHint?: string
  /**
   * Individual targets this task can't take right now, each with the reason
   * shown as the item's tooltip (e.g. Done on a task with subtasks).
   */
  disabledOptions?: Partial<Record<TaskWorkflowStatus, string>>
  /** When provided (recurring, not done), renders a "Skip occurrence…" item. */
  onSkipOccurrence?: () => void
}

export function StatusChip({
  value,
  onChange,
  disabled,
  disabledHint,
  disabledOptions,
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
          {WORKFLOW_STATUSES.map((status) => {
            const hint = disabledOptions?.[status]
            return (
              <button
                key={status}
                type="button"
                className={`chip-menu-item status-pill workflow-${status}`}
                aria-current={status === value ? 'true' : undefined}
                disabled={hint !== undefined}
                title={hint}
                onClick={() => {
                  close()
                  if (status !== value) onChange(status)
                }}
              >
                {workflowLabel(status)}
              </button>
            )
          })}
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
