import { useTrash } from './useTrash'

export function TrashPage() {
  const {
    trash,
    loading,
    error,
    restoreProjectById,
    restoreTaskById,
    restoreInboxById,
  } = useTrash()

  const isEmpty =
    trash.projects.length === 0 &&
    trash.tasks.length === 0 &&
    trash.inbox_items.length === 0

  return (
    <main>
      <h1>Trash</h1>
      <p>Recently deleted items. Restore anything you removed by mistake.</p>

      {error && <p role="alert">{error}</p>}
      {loading && <p>Loading…</p>}
      {!loading && isEmpty && <p>Trash is empty.</p>}

      {trash.projects.length > 0 && (
        <section>
          <h2>Projects ({trash.projects.length})</h2>
          <ul>
            {trash.projects.map((project) => (
              <li key={project.id}>
                <span>{project.name}</span>
                <button
                  type="button"
                  aria-label={`Restore project ${project.name}`}
                  onClick={() => void restoreProjectById(project.id)}
                >
                  Restore
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {trash.tasks.length > 0 && (
        <section>
          <h2>Tasks ({trash.tasks.length})</h2>
          <ul>
            {trash.tasks.map((task) => (
              <li key={task.id}>
                <span>{task.title}</span>
                <button
                  type="button"
                  aria-label={`Restore task ${task.title}`}
                  onClick={() => void restoreTaskById(task.id)}
                >
                  Restore
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {trash.inbox_items.length > 0 && (
        <section>
          <h2>Inbox items ({trash.inbox_items.length})</h2>
          <ul>
            {trash.inbox_items.map((item) => {
              const label = item.summary ?? item.raw_text.slice(0, 60)
              return (
                <li key={item.id}>
                  <span>
                    [{item.source}] {label}
                  </span>
                  <button
                    type="button"
                    aria-label={`Restore inbox item ${label}`}
                    onClick={() => void restoreInboxById(item.id)}
                  >
                    Restore
                  </button>
                </li>
              )
            })}
          </ul>
        </section>
      )}
    </main>
  )
}
