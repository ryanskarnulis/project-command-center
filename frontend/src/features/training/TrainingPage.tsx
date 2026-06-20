import { useEffect, useState } from 'react'
import type { MouseEvent } from 'react'
import { Check, Copy, GraduationCap, Search, SlidersHorizontal, Trash2 } from 'lucide-react'
import { useTraining } from './useTraining'
import { diffLines } from './diff'
import { formatRelative } from '../../utils/dates'
import type { TrainingExample } from '../../types/training'

/** Pretty-print a JSON string; fall back to the raw text if it isn't valid JSON
 *  (the extraction failure path stores raw, possibly-invalid model output). */
function prettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

/** Three-way status taxonomy for a training example. A correction is the
 *  highest-value signal, so it takes precedence over the accepted flag; an
 *  un-accepted example with no correction is an extraction/validation failure. */
function statusOf(example: TrainingExample): { label: string; tone: string } {
  if (example.corrected_output_json !== null) return { label: 'corrected', tone: 'tone-orange' }
  if (example.accepted) return { label: 'accepted', tone: 'tone-green' }
  return { label: 'extraction failure', tone: 'tone-red' }
}

/** Copy-to-clipboard button with a transient confirmation. Lives inside a
 *  <summary>, so it stops the click from toggling the surrounding <details>. */
function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  async function copy(e: MouseEvent) {
    e.preventDefault()
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard API unavailable (e.g. non-secure context) — silently skip.
    }
  }
  return (
    <button
      type="button"
      className="training-copy-btn"
      onClick={copy}
      aria-label={`Copy ${label}`}
    >
      {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}

/** A labeled, collapsible JSON/text block with a copy button in its header. */
function JsonBlock({ label, raw }: { label: string; raw: string }) {
  const text = prettyJson(raw)
  return (
    <details>
      <summary>
        <span>{label}</span>
        <CopyButton text={text} label={label} />
      </summary>
      <pre className="training-pre">{text}</pre>
    </details>
  )
}

/** Count added/removed lines between two outputs, for the row's collapsed
 *  preview badge. Mirrors what CorrectionDiff renders, minus the eq lines. */
function diffCounts(before: string, after: string): { add: number; del: number } {
  let add = 0
  let del = 0
  for (const line of diffLines(prettyJson(before), prettyJson(after))) {
    if (line.type === 'add') add++
    else if (line.type === 'del') del++
  }
  return { add, del }
}

/** Line-level diff of the original model output against the user's correction,
 *  open by default since it's the point of inspecting a corrected example. */
function CorrectionDiff({ before, after }: { before: string; after: string }) {
  const lines = diffLines(prettyJson(before), prettyJson(after))
  const marker: Record<string, string> = { eq: ' ', add: '+', del: '-' }
  return (
    <details open>
      <summary>
        <span>Correction diff</span>
      </summary>
      <pre className="training-pre training-diff">
        {lines.map((line, i) => (
          <span key={i} className={`training-diff-line is-${line.type}`}>
            {marker[line.type]} {line.text}
            {'\n'}
          </span>
        ))}
      </pre>
    </details>
  )
}

function ExampleRow({
  example,
  onDelete,
}: {
  example: TrainingExample
  onDelete: (id: number) => void
}) {
  const status = statusOf(example)
  const corrected = example.corrected_output_json
  const counts =
    corrected !== null ? diffCounts(example.model_output_json, corrected) : null

  // Move-to-trash button lives inside the <summary>; stop its click from
  // toggling the row open/closed.
  function handleDelete(e: MouseEvent) {
    e.preventDefault()
    e.stopPropagation()
    onDelete(example.id)
  }

  return (
    <li className="training-example">
      <details>
        <summary className="training-example-summary">
          <div className="training-example-id">
            <code>{example.task_name}</code>
            <span className={`status-pill ${status.tone}`}>{status.label}</span>
            {counts && (
              <span className="training-diff-count" aria-label="lines changed">
                <span className="diff-add">+{counts.add}</span>
                <span className="diff-del">−{counts.del}</span>
              </span>
            )}
          </div>
          <div className="training-example-meta">
            <span>{example.model_profile}</span>
            <span aria-hidden="true">·</span>
            <span>{example.model_name}</span>
            <span aria-hidden="true">·</span>
            <span title={example.created_at}>{formatRelative(example.created_at)}</span>
            <button
              type="button"
              className="training-delete-btn"
              aria-label={`Move ${example.task_name} example to trash`}
              onClick={handleDelete}
            >
              <Trash2 size={14} aria-hidden="true" />
            </button>
          </div>
        </summary>

        <div className="training-example-body">
          {corrected !== null && (
            <CorrectionDiff before={example.model_output_json} after={corrected} />
          )}
          <JsonBlock label="Input" raw={example.input_text} />
          <JsonBlock label="Model output" raw={example.model_output_json} />
          {corrected !== null && (
            <JsonBlock label="Corrected output" raw={corrected} />
          )}
        </div>
      </details>
    </li>
  )
}

export function TrainingPage() {
  const {
    stats,
    examples,
    loading,
    loadingMore,
    hasMore,
    error,
    filters,
    setFilters,
    loadMore,
    deleteExample,
  } = useTraining()

  // Move-to-trash is reversible (restore from the Trash page), so a light confirm
  // is enough — unlike the irreversible purge, which lives on the Trash page.
  function confirmDelete(id: number) {
    if (window.confirm('Move this example to trash? You can restore it from Trash.')) {
      void deleteExample(id)
    }
  }

  // Local search state, debounced into the (backend-side) filter so typing
  // doesn't fire a request per keystroke. Search runs server-side rather than
  // over the loaded page so it stays correct once pagination lands (Chunk D).
  const [searchInput, setSearchInput] = useState('')
  useEffect(() => {
    if (searchInput === (filters.search ?? '')) return
    const id = setTimeout(() => {
      setFilters({ ...filters, search: searchInput || undefined })
    }, 300)
    return () => clearTimeout(id)
  }, [searchInput, filters, setFilters])

  const pct = stats ? Math.min((stats.total / stats.goal) * 100, 100) : 0
  const acceptedPct =
    stats && stats.total > 0 ? Math.round((stats.accepted / stats.total) * 100) : 0
  const filtered =
    filters.task_name !== undefined ||
    filters.accepted !== undefined ||
    (filters.search ?? '') !== ''

  function clearFilters() {
    setSearchInput('')
    setFilters({})
  }

  return (
    <div className="training">
      <div className="section-heading">
        <div className="section-title">
          <GraduationCap size={19} aria-hidden="true" />
          <h1>Training data</h1>
        </div>
      </div>
      <p className="settings-note">
        Corrections you make in review are saved here as fine-tuning examples — the
        app's core output. Custom-model training becomes viable at {stats?.goal ?? 200}{' '}
        rows.
      </p>

      {error && <p className="error">Error: {error}</p>}

      {stats && (
        <section className="training-stats">
          <div className="training-stats-readout">
            <span className="training-readiness-label">Fine-tune readiness</span>
            <span className="status-pill tone-green">
              {stats.accepted} accepted · {acceptedPct}%
            </span>
          </div>
          <div className="training-progress-label">
            <strong>{stats.total}</strong> of {stats.goal} —{' '}
            {stats.remaining > 0
              ? `${stats.remaining} to go before fine-tuning is viable`
              : 'fine-tuning is viable'}
          </div>
          <div className="training-progress">
            <div className="training-progress-bar" style={{ width: `${pct}%` }} />
          </div>
          {filtered && (
            <div className="training-filtered-count">
              Showing {examples.length} (filtered)
            </div>
          )}
          {Object.keys(stats.by_task).length > 0 && (
            <div className="training-by-task">
              {Object.entries(stats.by_task).map(([task, stat]) => (
                <span key={task} className="training-task-chip">
                  <code>{task}</code> {stat.count}
                  <span className="training-chip-accepted">
                    {stat.accepted}/{stat.count} accepted
                  </span>
                </span>
              ))}
            </div>
          )}
        </section>
      )}

      <div className="task-filters" role="search" aria-label="Filter training examples">
        <div className="task-filters-header">
          <div className="task-filters-title">
            <SlidersHorizontal size={17} aria-hidden="true" />
            <strong>Filters</strong>
          </div>
          {filtered && (
            <button type="button" className="secondary-action" onClick={clearFilters}>
              Clear
            </button>
          )}
        </div>

        <label className="task-search-field">
          <span>Search</span>
          <div>
            <Search size={17} aria-hidden="true" />
            <input
              aria-label="Search training examples"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Input text or model output"
            />
          </div>
        </label>

        <div className="task-filter-grid">
          <label>
            <span>Task</span>
            <select
              aria-label="Filter by task"
              value={filters.task_name ?? ''}
              onChange={(e) =>
                setFilters({ ...filters, task_name: e.target.value || undefined })
              }
            >
              <option value="">All</option>
              {stats &&
                Object.keys(stats.by_task).map((task) => (
                  <option key={task} value={task}>
                    {task}
                  </option>
                ))}
            </select>
          </label>
          <label>
            <span>Status</span>
            <select
              aria-label="Filter by status"
              value={filters.accepted === undefined ? '' : String(filters.accepted)}
              onChange={(e) =>
                setFilters({
                  ...filters,
                  accepted:
                    e.target.value === '' ? undefined : e.target.value === 'true',
                })
              }
            >
              <option value="">All</option>
              <option value="true">accepted</option>
              <option value="false">rejected</option>
            </select>
          </label>
        </div>
      </div>

      {loading ? (
        <div className="page-loading">Loading examples…</div>
      ) : examples.length === 0 ? (
        <div className="empty-state">
          <GraduationCap size={20} aria-hidden="true" />
          No training examples yet. Review some inbox items to start the corpus.
        </div>
      ) : (
        <>
          <ul className="training-list">
            {examples.map((example) => (
              <ExampleRow key={example.id} example={example} onDelete={confirmDelete} />
            ))}
          </ul>
          {hasMore && (
            <div className="training-load-more">
              <button
                type="button"
                className="secondary-action"
                onClick={loadMore}
                disabled={loadingMore}
              >
                {loadingMore ? 'Loading…' : 'Load more'}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
