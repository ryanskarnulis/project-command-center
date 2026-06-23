import { type CSSProperties, Fragment, useMemo, useRef } from 'react'
import { Link } from 'react-router-dom'
import type { GanttProject } from '../../types/planning'
import type { GanttBar, GanttModel } from './ganttModel'
import { buildAxis } from './ganttAxis'
import type { ZoomLevel } from './ganttAxis'
import { useDragReschedule } from './useDragReschedule'
import { useBarResize } from './useBarResize'
import { useBucketDrag } from './useBucketDrag'
import { DependencyArrows } from './DependencyArrows'
import { computeViolations, violatingDependentIds } from './dependencyConflicts'
import { projectColorMap } from './projectColors'

// The custom CSS-grid Gantt renderer for the planning view. No third-party
// library (the frappe-gantt attempt was abandoned): one grid spans the whole
// chart so the time axis, day backgrounds, today marker, and bars all share the
// same columns. Bar geometry comes pre-resolved from `buildGanttModel`; the
// day->column bucketing comes from `buildAxis` (Slice 7 zoom) — this component
// only places bars into the columns the axis hands it. No scheduling math here.

/** A rendered grid row: a project section header, or a task bar. */
type Row =
  | { kind: 'group'; projectId: number }
  | { kind: 'bar'; bar: GanttBar }

/** The single most attention-worthy status class for a bar's fill. */
function barTone(bar: GanttModel['bars'][number]): string {
  if (bar.isBlocking) return 'tone-blocking'
  if (bar.isBlocked) return 'tone-blocked'
  if (bar.workflowStatus === 'in_progress') return 'tone-in-progress'
  return 'tone-open'
}

export function GanttChart({
  model,
  zoom = 'day',
  projects,
  onReschedule,
  onResize,
  onAutofix,
  onSchedule,
  onUnschedule,
}: {
  model: GanttModel
  /** Day/week/month column bucketing of the same date-space bars (Slice 7). */
  zoom?: ZoomLevel
  /**
   * When provided (global timeline, Slice 8), bars are grouped into labeled
   * per-project sections and colored by project. Omitted on the per-project
   * timeline, where every bar belongs to the one project — a single flat list.
   */
  projects?: GanttProject[]
  /** When provided, bars become draggable to set `scheduled_start`. */
  onReschedule?: (taskId: number, newStart: string) => void
  /** When provided, bars get a right-edge handle to set `estimated_minutes`. */
  onResize?: (taskId: number, newMinutes: number) => void
  /** When provided, dependency conflicts get a one-click Fix (sets `scheduled_start`). */
  onAutofix?: (taskId: number, newStart: string) => void
  /** When provided, unscheduled bucket items drag onto a column to set `scheduled_start`. */
  onSchedule?: (taskId: number, newStart: string) => void
  /** When provided, each bar gets a control to clear `scheduled_start` (take it off the timeline). */
  onUnschedule?: (taskId: number) => void
}) {
  const { bars, unscheduled } = model
  const gridRef = useRef<HTMLDivElement>(null)
  // Dependency violations (dependent starts on/before its blocker ends) drive the
  // arrow coloring, the bar warning class, and the conflicts panel — one shared
  // computation over the same bars the renderer places.
  const violations = useMemo(() => computeViolations(bars), [bars])
  const violatingIds = useMemo(
    () => violatingDependentIds(violations),
    [violations],
  )
  const barById = useMemo(() => new Map(bars.map((b) => [b.id, b])), [bars])
  // No-op when read-only (callbacks omitted); the hooks stay mounted either way
  // so the listener lifecycle is stable.
  const { onBarPointerDown, dragState, justDraggedRef } = useDragReschedule(
    gridRef,
    onReschedule ?? (() => {}),
  )
  const { onHandlePointerDown, resizeState, justResizedRef } = useBarResize(
    gridRef,
    onResize ?? (() => {}),
  )
  const draggable = onReschedule !== undefined
  const resizable = onResize !== undefined

  const axis = useMemo(() => buildAxis(bars, zoom), [bars, zoom])
  // Bucket-to-grid placement (Slice 9): drag an unscheduled item onto a column to
  // set its `scheduled_start` to that column's date. Hook stays mounted (no-op
  // callback when read-only) so its listener lifecycle is stable; it reads the
  // column geometry from the live grid, never computing dates here.
  const {
    onItemPointerDown,
    dragState: bucketDrag,
    justDraggedRef: justBucketDraggedRef,
  } = useBucketDrag(gridRef, axis?.columns ?? [], onSchedule ?? (() => {}))
  const schedulable = onSchedule !== undefined
  // When grouping by project, a project<->color map (for the bar accent) and a
  // project<->name map (for the section header). Empty when ungrouped.
  const colorByProject = useMemo(
    () => projectColorMap(projects ?? []),
    [projects],
  )
  const nameByProject = useMemo(
    () => new Map((projects ?? []).map((p) => [p.id, p.name])),
    [projects],
  )
  // The ordered list of grid rows: a group header before each project's first
  // bar (only when grouping), then that project's bars in model order. Without
  // `projects` it's just the bars — one flat list, unchanged from before.
  const rowPlan = useMemo<Row[]>(() => {
    if (!projects) return bars.map((bar) => ({ kind: 'bar', bar }))
    const rows: Row[] = []
    let lastProject: number | null = null
    for (const bar of bars) {
      if (bar.projectId !== lastProject) {
        rows.push({ kind: 'group', projectId: bar.projectId })
        lastProject = bar.projectId
      }
      rows.push({ kind: 'bar', bar })
    }
    return rows
  }, [bars, projects])

  // When there are no scheduled bars the axis is null (nothing to span), but the
  // unscheduled bucket still needs to render (e.g. after unscheduling the last bar).
  if (!axis) {
    if (unscheduled.length === 0) return null
    return (
      <div className="gantt-wrap">
        <div className="gantt-side">
          <aside className="gantt-unscheduled" aria-label="Unscheduled tasks">
            <h3>Unscheduled</h3>
            <p className="gantt-unscheduled-hint">No start or due date yet.</p>
            <ul>
              {unscheduled.map((task) => (
                <li key={task.id}>
                  <Link
                    to={`/tasks/${task.id}`}
                    draggable={false}
                    className={`gantt-unscheduled-item${schedulable ? ' is-draggable' : ''}`}
                    onPointerDown={schedulable ? (e) => onItemPointerDown(task, e) : undefined}
                  >
                    <span className="gantt-unscheduled-title">{task.name}</span>
                    {task.isBlocking && <span className="gantt-flag flag-blocking">Blocking</span>}
                    {task.isBlocked && <span className="gantt-flag flag-blocked">Blocked</span>}
                  </Link>
                </li>
              ))}
            </ul>
          </aside>
        </div>
      </div>
    )
  }

  const { columns, columnOf, todayIdx, daysPerColumn } = axis
  const colCount = columns.length

  return (
    <div className="gantt-wrap">
      <div
        ref={gridRef}
        className={`gantt gantt-zoom-${zoom}`}
        style={{ '--gantt-cols': colCount } as CSSProperties}
        role="table"
        aria-label="Project timeline"
      >
        {/* Header: corner + one cell per column (day/week/month per zoom) */}
        <div className="gantt-corner" style={{ gridColumn: 1, gridRow: 1 }}>
          Task
        </div>
        {columns.map((col, i) => (
          <div
            key={col.iso}
            className={`gantt-day-head${col.isWeekend ? ' is-weekend' : ''}${
              i === todayIdx ? ' is-today' : ''
            }`}
            style={{ gridColumn: i + 2, gridRow: 1 }}
          >
            {col.groupLabel && <span className="gantt-month">{col.groupLabel}</span>}
            <span className="gantt-dom">{col.label}</span>
          </div>
        ))}

        {/* Background columns (vertical grid lines, weekend + today shading) */}
        {columns.map((col, i) => (
          <div
            key={`bg-${col.iso}`}
            className={`gantt-col-bg${col.isWeekend ? ' is-weekend' : ''}${
              i === todayIdx ? ' is-today' : ''
            }${bucketDrag?.hoverCol === i ? ' is-drop-target' : ''}`}
            style={{ gridColumn: i + 2, gridRow: '2 / -1' }}
            aria-hidden="true"
          />
        ))}

        {/* One row per entry: a project section header (global view) or a bar's
            label cell + positioned bar (+ due marker). Row index drives gridRow. */}
        {rowPlan.map((row, r) => {
          if (row.kind === 'group') {
            const color = colorByProject.get(row.projectId)
            return (
              <div
                key={`group-${row.projectId}`}
                className="gantt-group-head"
                style={{ gridColumn: '1 / -1', gridRow: r + 2 }}
                role="rowheader"
              >
                <span
                  className="gantt-group-swatch"
                  style={{ background: color }}
                  aria-hidden="true"
                />
                <span className="gantt-group-name">
                  {nameByProject.get(row.projectId) ?? 'Project'}
                </span>
              </div>
            )
          }
          const { bar } = row
          const barColor = projects ? colorByProject.get(bar.projectId) : undefined
          const offset = columnOf(bar.start)
          const baseLen = columnOf(bar.end) - offset + 1
          // While resizing, preview the new span — `newSpan` is in days, so map it
          // to whole columns at the current zoom (one column = `daysPerColumn`).
          const len =
            resizeState?.barId === bar.id
              ? Math.max(1, Math.ceil(resizeState.newSpan / daysPerColumn))
              : baseLen
          const dueIdx =
            bar.dueDate && bar.dueDate >= columns[0].iso
              ? columnOf(bar.dueDate)
              : -1
          // A parent's estimate is the sum of its subtasks (server rollup), so it
          // is not directly settable — no resize handle, and a tooltip says why.
          const barResizable = resizable && !bar.hasSubtasks
          const tooltip = `${bar.name} · ${bar.start} → ${bar.end}${
            bar.dueDate ? ` · due ${bar.dueDate}` : ''
          }${bar.hasSubtasks ? ' · estimate rolls up from subtasks' : ''}`
          return (
            <Fragment key={bar.id}>
              <div
                className="gantt-label"
                style={{
                  gridColumn: 1,
                  gridRow: r + 2,
                  paddingLeft: 8 + bar.depth * 16,
                }}
                role="rowheader"
                title={bar.name}
              >
                {bar.depth > 0 && <span className="gantt-label-tick" aria-hidden="true" />}
                <span className="gantt-label-text">{bar.name}</span>
              </div>
              {dueIdx >= 0 && dueIdx < colCount && (
                <div
                  className="gantt-due"
                  style={{ gridColumn: dueIdx + 2, gridRow: r + 2 }}
                  aria-hidden="true"
                />
              )}
              <Link
                to={`/tasks/${bar.id}`}
                data-bar-id={bar.id}
                // Anchors are natively draggable; that hijacks the pointer stream
                // (browser DnD) so our window pointermove/up never fire. Opt out.
                draggable={false}
                className={`gantt-bar ${barTone(bar)}${bar.conflict ? ' is-conflict' : ''}${
                  violatingIds.has(bar.id) ? ' is-dep-conflict' : ''
                }${draggable ? ' is-draggable' : ''}${
                  barColor ? ' has-project-color' : ''
                }${dragState?.barId === bar.id ? ' is-dragging' : ''}${
                  resizeState?.barId === bar.id ? ' is-resizing' : ''
                }`}
                style={
                  {
                    gridColumn: `${offset + 2} / span ${len}`,
                    gridRow: r + 2,
                    transform:
                      dragState?.barId === bar.id && dragState.deltaDays !== 0
                        ? `translateX(${dragState.deltaPx}px)`
                        : undefined,
                    // Project accent (global view): a left border the status tones
                    // layer behind, so blocked/blocking/conflict signals stay legible.
                    ...(barColor ? { '--bar-color': barColor } : {}),
                  } as CSSProperties
                }
                title={tooltip}
                onPointerDown={
                  draggable
                    ? (e) => onBarPointerDown(bar, e, daysPerColumn)
                    : undefined
                }
                onClick={
                  draggable || resizable
                    ? (e) => {
                        // Swallow the click that ends a real drag or resize so it
                        // doesn't navigate; a plain click still falls through.
                        if (justDraggedRef.current || justResizedRef.current) {
                          e.preventDefault()
                          justDraggedRef.current = false
                          justResizedRef.current = false
                        }
                      }
                    : undefined
                }
              >
                <span className="gantt-bar-text">{bar.name}</span>
                {onUnschedule && (
                  <button
                    type="button"
                    className="gantt-unschedule"
                    aria-label="Unschedule task"
                    title="Take off the timeline"
                    // Don't let the press start a drag or navigate the bar link.
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => {
                      e.preventDefault()
                      e.stopPropagation()
                      onUnschedule(bar.id)
                    }}
                  >
                    ×
                  </button>
                )}
                {barResizable && (
                  <span
                    className="gantt-resize-handle"
                    aria-hidden="true"
                    onPointerDown={(e) => {
                      // Stop propagation so a handle press doesn't also start a move.
                      e.stopPropagation()
                      onHandlePointerDown(bar, e, daysPerColumn)
                    }}
                  />
                )}
              </Link>
            </Fragment>
          )
        })}

        {/* Dependency arrows overlay the bars; measured from the rendered rects. */}
        <DependencyArrows bars={bars} violations={violations} />
      </div>

      {(unscheduled.length > 0 || violations.length > 0) && (
        <div className="gantt-side">
          {violations.length > 0 && (
            <aside className="gantt-conflicts" aria-label="Scheduling conflicts">
              <h3>Conflicts</h3>
              <p className="gantt-conflicts-hint">
                A task is scheduled before the task it depends on finishes.
              </p>
              <ul>
                {violations.map((v) => {
                  const dependent = barById.get(v.dependentId)
                  const blocker = barById.get(v.blockerId)
                  if (!dependent || !blocker) return null
                  return (
                    <li key={`${v.dependentId}->${v.blockerId}`}>
                      <span className="gantt-conflict-text">
                        <Link to={`/tasks/${dependent.id}`}>{dependent.name}</Link>{' '}
                        starts before{' '}
                        <Link to={`/tasks/${blocker.id}`}>{blocker.name}</Link>{' '}
                        finishes
                      </span>
                      {onAutofix && (
                        <button
                          type="button"
                          className="gantt-conflict-fix"
                          onClick={() => onAutofix(v.dependentId, v.suggestedStart)}
                          title={`Move to ${v.suggestedStart}`}
                        >
                          Fix
                        </button>
                      )}
                    </li>
                  )
                })}
              </ul>
            </aside>
          )}

          {unscheduled.length > 0 && (
            <aside className="gantt-unscheduled" aria-label="Unscheduled tasks">
              <h3>Unscheduled</h3>
              <p className="gantt-unscheduled-hint">No start or due date yet.</p>
              <ul>
                {unscheduled.map((task) => (
                  <li key={task.id}>
                    <Link
                      to={`/tasks/${task.id}`}
                      // Anchors are natively draggable, which hijacks the pointer
                      // stream so our window pointermove/up never fire. Opt out.
                      draggable={false}
                      className={`gantt-unscheduled-item${
                        schedulable ? ' is-draggable' : ''
                      }${bucketDrag?.taskId === task.id ? ' is-dragging' : ''}`}
                      onPointerDown={
                        schedulable ? (e) => onItemPointerDown(task, e) : undefined
                      }
                      onClick={
                        schedulable
                          ? (e) => {
                              // Swallow the click that ends a real drag so it
                              // doesn't navigate; a plain click falls through.
                              if (justBucketDraggedRef.current) {
                                e.preventDefault()
                                justBucketDraggedRef.current = false
                              }
                            }
                          : undefined
                      }
                    >
                      <span className="gantt-unscheduled-title">{task.name}</span>
                      {task.isBlocking && <span className="gantt-flag flag-blocking">Blocking</span>}
                      {task.isBlocked && <span className="gantt-flag flag-blocked">Blocked</span>}
                    </Link>
                  </li>
                ))}
              </ul>
            </aside>
          )}
        </div>
      )}

      {/* Floating ghost following the pointer while dragging a bucket item. */}
      {bucketDrag && (
        <div
          className="gantt-drag-ghost"
          style={{ left: bucketDrag.clientX, top: bucketDrag.clientY }}
          aria-hidden="true"
        >
          {bucketDrag.name}
        </div>
      )}
    </div>
  )
}
