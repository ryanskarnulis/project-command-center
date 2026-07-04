import { Modal } from '../../components/Modal'

interface Props {
  /** When set, the confirm is open and names the occurrence being skipped. */
  taskTitle: string | null
  onConfirm: () => void
  onCancel: () => void
}

/**
 * Shared "skip this occurrence" confirmation, reused by the task list card, the
 * Today page, and the series timeline so the copy stays consistent. Skipping
 * soft-deletes the current occurrence and spawns the next one — the same wording
 * the task detail view uses inline.
 */
export function SkipOccurrenceConfirm({ taskTitle, onConfirm, onCancel }: Props) {
  return (
    <Modal open={taskTitle !== null} title="Skip occurrence" onClose={onCancel}>
      <div className="skip-confirm-body">
        <p>
          Skip {taskTitle ? <strong>{taskTitle}</strong> : 'this occurrence'} — it&apos;ll
          move to trash and the next occurrence will be created. Continue?
        </p>
        <div className="skip-confirm-actions">
          <button type="button" className="secondary-action" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" onClick={onConfirm}>
            Skip occurrence
          </button>
        </div>
      </div>
    </Modal>
  )
}
