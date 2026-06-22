import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { AsyncState } from '../../components/AsyncState'
import { getProject } from '../../api/projects'
import type { Project } from '../../types/project'
import { ProjectTabs } from '../projects/ProjectTabs'
import { GanttChart } from './GanttChart'
import { buildGanttModel } from './ganttModel'
import { useProjectGantt } from './useProjectGantt'
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

  const { data, loading, error, reschedule, resize } = useProjectGantt(id)
  const model = useMemo(() => (data ? buildGanttModel(data) : null), [data])

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
            Scheduled work by start date and estimate — drag a bar to reschedule,
            its right edge to re-estimate.
          </p>
        </div>
      </header>
      <ProjectTabs projectId={id} />

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
            onReschedule={reschedule}
            onResize={resize}
            // Autofix is a single-task reschedule to just after the blocker ends —
            // the existing optimistic PATCH + revert + toast path. No cascade (that
            // is the later Python auto-shift slice).
            onAutofix={reschedule}
          />
        )}
      </AsyncState>
    </main>
  )
}
