import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Sparkles } from 'lucide-react'
import { Badge, type BadgeTone } from '../../components/Badge'
import { Card } from '../../components/Card'
import { AsyncState } from '../../components/AsyncState'
import { useToast } from '../../components/ToastContext'
import { markTaskDone } from '../../api/tasks'
import type { SearchKind, SearchResultItem } from '../../types/search'
import { parseCommand, type HintVerb } from './parseCommand'
import { useSearch } from './useSearch'

// Per-kind display metadata for plain search results. The bar is intentionally
// generic so routing/labels live in data, not branching JSX.
const KIND_META: Record<
  SearchKind,
  { label: string; tone: BadgeTone; path: (item: SearchResultItem) => string }
> = {
  project: { label: 'Project', tone: 'blue', path: (i) => `/projects/${i.id}` },
  task: { label: 'Task', tone: 'purple', path: (i) => `/tasks/${i.id}` },
}

// One dropdown row, whatever produced it (search hit, /done match).
// Keyboard nav iterates these uniformly; each carries its own `onSelect`.
interface ActionRow {
  key: string
  badge: { label: string; tone: BadgeTone }
  title: string
  subtitle?: string | null
  onSelect: () => void
  disabled?: boolean
}

interface ActionGroup {
  label: string | null
  rows: ActionRow[]
}

const HINT_TEXT: Record<HintVerb, string> = {
  root: 'Type after the slash to run a command.',
  done: 'Type a task to find, e.g. /done audit firewall rules',
}

export function CommandSearch() {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const { notify } = useToast()
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const command = useMemo(() => parseCommand(query), [query])

  // Only search/done hit the backend; hint states pass a blank query so
  // `useSearch` short-circuits without an API call.
  const searchQuery =
    command.kind === 'search' || command.kind === 'done' ? command.query : ''
  const { results, loading, error } = useSearch(searchQuery)

  const reset = useCallback(() => {
    setOpen(false)
    setQuery('')
  }, [])

  const goto = useCallback(
    (path: string) => {
      navigate(path)
      reset()
    },
    [navigate, reset],
  )

  // /done: complete the chosen task via the dedicated endpoint (preserves recurrence).
  const runDone = useCallback(
    async (item: SearchResultItem) => {
      try {
        await markTaskDone(item.id)
        notify('success', `Completed “${item.title}”.`)
        reset()
      } catch (e: unknown) {
        notify(
          'error',
          e instanceof Error ? e.message : `Couldn't complete “${item.title}”.`,
        )
      }
    },
    [notify, reset],
  )

  const groups: ActionGroup[] = useMemo(() => {
    if (command.kind === 'done') {
      // Only accepted, not-yet-done tasks are valid completion targets; candidates
      // and already-done tasks are filtered out (status fields come from search).
      const rows = results.tasks
        .filter(
          (t) =>
            t.review_status === 'accepted' && t.workflow_status !== 'done',
        )
        .map<ActionRow>((t) => ({
          key: `done-${t.id}`,
          badge: { label: 'Task', tone: 'purple' },
          title: t.title,
          subtitle: t.subtitle,
          onSelect: () => void runDone(t),
        }))
      return [{ label: 'Complete a task', rows }]
    }

    if (command.kind === 'search') {
      return (
        [
          { label: 'Projects', items: results.projects },
          { label: 'Tasks', items: results.tasks },
        ] as const
      )
        .filter((g) => g.items.length > 0)
        .map<ActionGroup>((g) => ({
          label: g.label,
          rows: g.items.map<ActionRow>((item) => ({
            key: `${item.kind}-${item.id}`,
            badge: {
              label: KIND_META[item.kind].label,
              tone: KIND_META[item.kind].tone,
            },
            title: item.title,
            subtitle: item.subtitle,
            onSelect: () => goto(KIND_META[item.kind].path(item)),
          })),
        }))
    }

    return [] // hint: rendered separately, nothing selectable
  }, [command, results, runDone, goto])

  const flat = useMemo(() => groups.flatMap((g) => g.rows), [groups])
  const activeKey = useMemo(
    () => JSON.stringify([query, flat.map((row) => row.key)]),
    [query, flat],
  )
  const [activeState, setActiveState] = useState({ key: '', index: -1 })
  const activeIndex = activeState.key === activeKey ? activeState.index : -1
  const setCurrentActiveIndex = useCallback(
    (next: number | ((current: number) => number)) => {
      setActiveState((state) => {
        const current = state.key === activeKey ? state.index : -1
        return {
          key: activeKey,
          index: typeof next === 'function' ? next(current) : next,
        }
      })
    },
    [activeKey],
  )

  // Close the dropdown when focus/click leaves the bar.
  useEffect(() => {
    function onPointerDown(e: MouseEvent) {
      if (!containerRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [])

  // Global Cmd/Ctrl+K focuses the bar from anywhere (matches the `Cmd K` hint).
  // preventDefault stops the browser binding Ctrl+K to its own search/URL bar.
  useEffect(() => {
    function onKeydown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        inputRef.current?.focus()
        inputRef.current?.select()
        setOpen(true)
      }
    }
    window.addEventListener('keydown', onKeydown)
    return () => window.removeEventListener('keydown', onKeydown)
  }, [])

  const trimmed = query.trim()
  const showDropdown = open && trimmed !== ''
  const isHint = command.kind === 'hint'
  const showAsyncState = command.kind === 'search' || command.kind === 'done'
  const emptyLabel =
    command.kind === 'done'
      ? `No open tasks match “${command.query}”.`
      : `No matches for “${trimmed}”.`

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setOpen(true)
      setCurrentActiveIndex((i) => Math.min(i + 1, flat.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setCurrentActiveIndex((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      const row = flat[activeIndex]
      if (row && !row.disabled) {
        e.preventDefault()
        row.onSelect()
      }
    } else if (e.key === 'Escape') {
      setOpen(false)
      e.currentTarget.blur()
    }
  }

  let globalIndex = -1

  return (
    <div className="command-search-wrap" ref={containerRef}>
      <div className="command-search">
        <Sparkles size={18} aria-hidden="true" />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder="Search, or type / for commands…"
          aria-label="Search projects and tasks, or run a slash command"
          role="combobox"
          aria-expanded={showDropdown}
          aria-controls="command-search-results"
        />
        <kbd>Cmd K</kbd>
      </div>

      {showDropdown && (
        <Card
          as="div"
          className="command-search-dropdown"
          id="command-search-results"
          role="listbox"
        >
          {isHint ? (
            <div className="command-search-group">
              <p className="command-search-group-label">Commands</p>
              <ul>
                <li className="command-search-hint">
                  <Badge tone="purple">/done</Badge>
                  <span>complete a task</span>
                </li>
              </ul>
              {command.kind === 'hint' && command.verb !== 'root' && (
                <p className="async-empty">{HINT_TEXT[command.verb]}</p>
              )}
            </div>
          ) : (
            <AsyncState
              loading={showAsyncState && loading}
              error={showAsyncState ? error : null}
              isEmpty={showAsyncState && flat.length === 0}
              loadingLabel="Searching…"
              emptyLabel={emptyLabel}
            >
              {groups.map((group) => (
                <div
                  key={group.label ?? '_'}
                  className="command-search-group"
                >
                  {group.label && (
                    <p className="command-search-group-label">{group.label}</p>
                  )}
                  <ul>
                    {group.rows.map((row) => {
                      globalIndex += 1
                      const index = globalIndex
                      return (
                        <li key={row.key}>
                          <button
                            type="button"
                            role="option"
                            aria-selected={index === activeIndex}
                            disabled={row.disabled}
                            className={
                              index === activeIndex
                                ? 'command-search-result active'
                                : 'command-search-result'
                            }
                            // Pointer enter keeps mouse + keyboard highlight in sync.
                            onMouseEnter={() => setCurrentActiveIndex(index)}
                            onClick={row.onSelect}
                          >
                            <Badge tone={row.badge.tone}>
                              {row.badge.label}
                            </Badge>
                            <span className="command-search-result-text">
                              <span className="command-search-result-title">
                                {row.title}
                              </span>
                              {row.subtitle && (
                                <span className="command-search-result-subtitle">
                                  {row.subtitle}
                                </span>
                              )}
                            </span>
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                </div>
              ))}
            </AsyncState>
          )}
        </Card>
      )}
    </div>
  )
}
