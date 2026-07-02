import { useEffect, useRef } from 'react'
import { X } from 'lucide-react'
import { TaskDetailView } from '../TaskDetailView'

interface Props {
  taskId: number
  onClose: () => void
  onMutated?: () => void
}

/** Right-side slide-over hosting the task detail surface over the current list. */
export function TaskPeekPanel({ taskId, onClose, onMutated }: Props) {
  const asideRef = useRef<HTMLElement>(null)

  // Focus the panel when it opens (or repoints) so Esc works immediately.
  useEffect(() => {
    asideRef.current?.focus()
  }, [taskId])

  // Esc closes the panel — unless a modal (EditScopeModal, task form, …) is
  // stacked above it; the modal owns Esc while mounted. The DOM check is
  // order-independent, unlike relying on listener registration order. Chip
  // popovers stopPropagation their own Esc before it reaches the window.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== 'Escape') return
      if (document.querySelector('.modal-overlay')) return
      onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="peek-overlay" onMouseDown={onClose}>
      <aside
        ref={asideRef}
        className="peek-panel"
        role="dialog"
        aria-label="Task details"
        tabIndex={-1}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          className="peek-close"
          aria-label="Close panel"
          onClick={onClose}
        >
          <X size={18} aria-hidden="true" />
        </button>
        <TaskDetailView taskId={taskId} onClose={onClose} onMutated={onMutated} />
      </aside>
    </div>
  )
}
