import { Archive, RotateCcw, Trash2 } from 'lucide-react'
import { BottomSheet } from '../../components/BottomSheet'

interface Props {
  /** A closed project offers Reopen in the first row instead of Close. */
  closed: boolean
  onClose: () => void
  onToggleClosed: () => void
  onDelete: () => void
}

/**
 * M06f lifecycle sheet, opened from the title row's `⋯`. Two rows and no
 * header or cancel: the scrim dismisses. Never rendered for a protected
 * project — both actions are unavailable there, so the control is not either.
 */
export function ProjectActionSheet({ closed, onClose, onToggleClosed, onDelete }: Props) {
  return (
    <BottomSheet label="Project actions" className="project-action-sheet" handleLabel="Close actions" onClose={onClose}>
      <button type="button" className="project-action" onClick={() => { onClose(); onToggleClosed() }}>
        {closed ? <RotateCcw size={17} aria-hidden="true" /> : <Archive size={17} aria-hidden="true" />}
        <span>{closed ? 'Reopen project' : 'Close project'}</span>
      </button>
      <button type="button" className="project-action danger" onClick={() => { onClose(); onDelete() }}>
        <Trash2 size={17} aria-hidden="true" />
        <span>Delete project</span>
      </button>
    </BottomSheet>
  )
}
