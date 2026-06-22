import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { AsyncState } from '../../components/AsyncState'
import { getProject } from '../../api/projects'
import type { Project } from '../../types/project'
import { ProjectTabs } from '../projects/ProjectTabs'
import { GanttChart } from './GanttChart'
import { buildGanttModel } from './ganttModel'
import { useProjectGantt } from './useProjectGantt'
import { useWhatIf } from './useWhatIf'
import './planning.css'

/**
 * The per-project Timeline tab (`/projects/:id/timeline`): a read-only Gantt of
 * accepted, not-done tasks placed by `scheduled_start` + estimate. Fetches the
 * planning payload, maps it to renderer-ready bars (`buildGanttModel`), and hands
 * it to the custom `GanttChart`. Loading/error/empty via the shared `AsyncState`.
 */
export function TimelinePage() {
  const { projectId } = useParams<{ projectId: string }>()
  const id = Number(projectId)

  const { data, loading, error, reschedule, resize, refetch } =
    useProjectGantt(id)
  const whatIf = useWhatIf(id, refetch)
  // In what-if mode the chart renders the staged/previewed schedule (the backend
  // returns the shifted starts); otherwise the real payload. Either way the model
  // is built the same way — bar geometry doesn't know it's a hypothetical.
  const model = useMemo(() => {
    if (!data) return null
    return buildGanttModel(whatIf.active ? whatIf.applyPreview(data) : data)
  }, [data, whatIf])

  // The planning payload carries no project name; fetch it for the header only.
  const [project, setProject] = useState<Project | null>(null)
  useEffect(() => {
    let active = true
    getProject(id)
      .then((p) => active && setProject(p))
      .catch(() => active && setProject(null))
    return () => {
      active = false
    }
  }, [id])

  const isEmpty =
    model !== null && model.bars.length === 0 && model.unscheduled.length === 0

  return (
    <main className="page planning-page">
      <p className="breadcrumb">
        <Link to="/projects">← Projects</Link>
      </p>
      <header className="page-header">
        <div className="page-title">
          <h1>{project?.name ?? 'Timeline'}</h1>
          <p className="page-subtitle">
            {whatIf.active
              ? 'What-if mode: drag to stage changes — nothing is saved until you apply.'
              : 'Scheduled work by start date and estimate — drag a bar to reschedule, its right edge to re-estimate.'}
          </p>
        </div>
        {model && !isEmpty && !whatIf.active && (
          <button
            type="button"
            className="whatif-toggle"
            onClick={whatIf.enter}
          >
            What-if mode
          </button>
        )}
      </header>
      <ProjectTabs projectId={id} />

      {whatIf.active && (
        <div className="whatif-bar" role="status">
          <span className="whatif-bar-label">
            {whatIf.stagedCount === 0
              ? 'What-if mode — drag a bar or resize to stage a change.'
              : `${whatIf.stagedCount} staged change${
                  whatIf.stagedCount === 1 ? '' : 's'
                }${whatIf.pending ? ' · previewing…' : ''}`}
          </span>
          <span className="whatif-bar-actions">
            <button
              type="button"
              className="whatif-apply"
              disabled={whatIf.stagedCount === 0 || whatIf.pending}
              onClick={() => void whatIf.commit()}
            >
              Apply
            </button>
            <button
              type="button"
              className="whatif-discard"
              onClick={whatIf.discard}
            >
              Discard
            </button>
          </span>
        </div>
      )}

      <AsyncState
        loading={loading}
        error={error}
        isEmpty={isEmpty}
        loadingLabel="Loading timeline…"
        emptyLabel="No accepted tasks to schedule yet."
      >
        {model && !isEmpty && (
          <GanttChart
            model={model}
            // In what-if mode a drag/resize/Fix *stages* the change (re-previewed
            // server-side) rather than persisting; otherwise it PATCHes for real.
            onReschedule={whatIf.active ? whatIf.stageStart : reschedule}
            onResize={whatIf.active ? whatIf.stageEstimate : resize}
            onAutofix={whatIf.active ? whatIf.stageStart : reschedule}
          />
        )}
      </AsyncState>
    </main>
  )
}
