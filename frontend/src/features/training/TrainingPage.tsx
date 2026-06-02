import { useTraining } from './useTraining'
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

function ExampleRow({ example }: { example: TrainingExample }) {
  return (
    <li className="training-example">
      <div className="training-example-header">
        <code>{example.task_name}</code>
        <span className={example.accepted ? 'training-accepted' : 'training-rejected'}>
          {example.accepted ? 'accepted' : 'rejected'}
        </span>
        <span className="settings-meta">{example.model_name}</span>
      </div>

      <details>
        <summary>Input</summary>
        <pre className="training-pre">{example.input_text}</pre>
      </details>
      <details>
        <summary>Model output</summary>
        <pre className="training-pre">{prettyJson(example.model_output_json)}</pre>
      </details>
      {example.corrected_output_json !== null && (
        <details>
          <summary>Corrected output</summary>
          <pre className="training-pre">{prettyJson(example.corrected_output_json)}</pre>
        </details>
      )}
    </li>
  )
}

export function TrainingPage() {
  const { stats, examples, loading, error, filters, setFilters } = useTraining()

  const pct = stats ? Math.min((stats.total / stats.goal) * 100, 100) : 0

  return (
    <div className="training">
      <h1>Training data</h1>
      <p className="settings-note">
        Corrections you make in review are saved here as fine-tuning examples — the
        app's core output. Custom-model training becomes viable at {stats?.goal ?? 200}{' '}
        rows.
      </p>

      {error && <p className="error">Error: {error}</p>}

      {stats && (
        <section className="training-stats">
          <div className="training-progress-label">
            <strong>{stats.total}</strong> of {stats.goal} —{' '}
            {stats.remaining > 0
              ? `${stats.remaining} to go before fine-tuning is viable`
              : 'fine-tuning is viable'}{' '}
            ({stats.accepted} accepted)
          </div>
          <div className="training-progress">
            <div className="training-progress-bar" style={{ width: `${pct}%` }} />
          </div>
          {Object.keys(stats.by_task).length > 0 && (
            <ul className="training-by-task">
              {Object.entries(stats.by_task).map(([task, count]) => (
                <li key={task}>
                  <code>{task}</code>: {count}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <section className="training-filters">
        <label>
          Task
          <select
            value={filters.task_name ?? ''}
            onChange={(e) =>
              setFilters({ ...filters, task_name: e.target.value || undefined })
            }
          >
            <option value="">all</option>
            {stats &&
              Object.keys(stats.by_task).map((task) => (
                <option key={task} value={task}>
                  {task}
                </option>
              ))}
          </select>
        </label>
        <label>
          Status
          <select
            value={filters.accepted === undefined ? '' : String(filters.accepted)}
            onChange={(e) =>
              setFilters({
                ...filters,
                accepted: e.target.value === '' ? undefined : e.target.value === 'true',
              })
            }
          >
            <option value="">all</option>
            <option value="true">accepted</option>
            <option value="false">rejected</option>
          </select>
        </label>
      </section>

      {loading ? (
        <p>Loading examples…</p>
      ) : examples.length === 0 ? (
        <p>No training examples yet. Review some inbox items to start the corpus.</p>
      ) : (
        <ul className="training-list">
          {examples.map((example) => (
            <ExampleRow key={example.id} example={example} />
          ))}
        </ul>
      )}
    </div>
  )
}
