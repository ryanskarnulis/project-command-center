import { Modal } from '../../components/Modal'
import type { EditScope } from '../../types/task'

interface Props {
  open: boolean
  onChoose: (scope: EditScope) => void
  onCancel: () => void
}

/**
 * Asks how an edit to a recurring task should apply: just this occurrence, or
 * this and every future one in the series. Shown before saving a forward-scopable
 * field change (title, description, priority, estimate, repeat interval) on a task
 * that belongs to a recurrence chain.
 */
export function EditScopeModal({ open, onChoose, onCancel }: Props) {
  return (
    <Modal open={open} title="Apply to recurring task" onClose={onCancel}>
      <p>This task repeats. Where should this change apply?</p>
      <div className="edit-scope-actions">
        <button type="button" onClick={() => onChoose('this')}>
          This task only
        </button>
        <button type="button" onClick={() => onChoose('future')}>
          This and all future occurrences
        </button>
      </div>
    </Modal>
  )
}
