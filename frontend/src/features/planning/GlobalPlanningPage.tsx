import { useMemo, useState } from 'react'
import { AsyncState } from '../../components/AsyncState'
import { GanttChart } from './GanttChart'
import type { ZoomLevel } from './ganttAxis'
import { buildGanttModel } from './ganttModel'
import { projectColor } from './projectColors'
import { useGlobalGantt } from './useGlobalGantt'
import './planning.css'

/**
 * The global planning surface (`/planning`, Slice 8): a cross-project Gantt of
 * every project's accepted, not-done scheduled work on one axis, bars grouped and
 * colored by project. Structurally like `TimelinePage` (zoom + `AsyncState` +
 * `GanttChart`) but project-agnostic and without what-if. Drag/resize reuse the
 * same task PATCH (which cascades across project boundaries server-side); the
 * frontend only refetches to surface the shifts.
 */
export function GlobalPlanningPage() {
  const { data, loading, error, reschedule, resize } = useGlobalGantt()
  const [zoom, setZoom] = useState<ZoomLevel>('day')

  const model = useMemo(() => (data ? buildGanttModel(data) : null), [data])

  const isEmpty =
    model !== null && model.bars.length === 0 && model.unscheduled.length === 0

  return (
    <main className="page planning-page">
      <header className="page-header">
        <div className="page-title">
          <h1>Planning</h1>
          <p className="page-subtitle">
            Scheduled work across every project — drag a bar to reschedule, its
            right edge to re-estimate. Dependents shift even across projects.
          </p>
          {data && data.projects.length > 0 && (
            <ul className="planning-legend" aria-label="Projects">
              {data.projects.map((project, i) => (
                <li key={project.id} className="planning-legend-item">
                  <span
                    className="planning-legend-swatch"
                    style={{ background: projectColor(i) }}
                    aria-hidden="true"
                  />
                  {project.name}
                </li>
              ))}
            </ul>
          )}
        </div>
        {model && !isEmpty && (
          <div className="timeline-controls">
            <div
              className="gantt-zoom"
              role="group"
              aria-label="Timeline zoom level"
            >
              {(['day', 'week', 'month'] as const).map((level) => (
                <button
                  key={level}
                  type="button"
                  className={`gantt-zoom-btn${zoom === level ? ' is-active' : ''}`}
                  aria-pressed={zoom === level}
                  onClick={() => setZoom(level)}
                >
                  {level[0].toUpperCase() + level.slice(1)}
                </button>
              ))}
            </div>
          </div>
        )}
      </header>

      <AsyncState
        loading={loading}
        error={error}
        isEmpty={isEmpty}
        loadingLabel="Loading planning…"
        emptyLabel="No scheduled work across your projects yet."
      >
        {model && !isEmpty && data && (
          <GanttChart
            model={model}
            zoom={zoom}
            projects={data.projects}
            onReschedule={reschedule}
            onResize={resize}
            onAutofix={reschedule}
          />
        )}
      </AsyncState>
    </main>
  )
}
