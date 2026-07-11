import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Sparkles } from 'lucide-react'
import { Badge, type BadgeTone } from '../../components/Badge'
import { Card } from '../../components/Card'
import { AsyncState } from '../../components/AsyncState'
import { SpiderMark } from '../../components/SpiderMark'
import type { SearchKind, SearchResultItem } from '../../types/search'
import { InlineAgentExchange } from './InlineAgentExchange'
import { useInlineAgentAsk } from './useInlineAgentAsk'
import { useSearch } from './useSearch'

// Per-kind display metadata for search results. The bar is intentionally
// generic so routing/labels live in data, not branching JSX.
const KIND_META: Record<
  SearchKind,
  { label: string; tone: BadgeTone; path: (item: SearchResultItem) => string }
> = {
  project: { label: 'Project', tone: 'blue', path: (i) => `/projects/${i.id}` },
  task: { label: 'Task', tone: 'purple', path: (i) => `/tasks/${i.id}` },
}

// One dropdown row. Keyboard nav iterates these uniformly; each carries its
// own `onSelect`.
interface ActionRow {
  key: string
  badge: { label: string; tone: BadgeTone }
  title: string
  subtitle?: string | null
  onSelect: () => void
}

interface ActionGroup {
  label: string | null
  rows: ActionRow[]
}

export function CommandSearch() {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const { results, loading, error } = useSearch(query)
  const { state: askState, ask, dismiss } = useInlineAgentAsk()
  const asking = askState.phase === 'pending'

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

  const groups: ActionGroup[] = useMemo(
    () =>
      (
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
        })),
    [results, goto],
  )

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

  const trimmed = query.trim()

  // Plain Enter / footer click: post the bar's text as a fresh agent
  // conversation and render the exchange inline. On success the bar clears
  // and re-enables so typing resumes live search; on error the text stays
  // for a retry.
  const submitAsk = useCallback(async () => {
    if (trimmed === '' || asking) return
    const ok = await ask(trimmed)
    if (ok) setQuery('')
  }, [trimmed, asking, ask])

  const dismissExchange = useCallback(() => dismiss(), [dismiss])

  // Close the dropdown (and any shown exchange) when focus/click leaves the
  // bar. A pending run stays visible — `dismiss` no-ops while in flight.
  useEffect(() => {
    function onPointerDown(e: MouseEvent) {
      if (!containerRef.current?.contains(e.target as Node)) {
        setOpen(false)
        dismissExchange()
      }
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [dismissExchange])

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

  // The exchange panel replaces the results dropdown while an ask is
  // pending/shown; typing again dismisses it and resumes live search.
  const exchangeVisible = askState.phase !== 'idle'
  const showDropdown = open && trimmed !== '' && !exchangeVisible

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
      if (showDropdown && row) {
        e.preventDefault()
        row.onSelect()
      } else if (trimmed !== '' && !asking) {
        e.preventDefault()
        void submitAsk()
      }
    } else if (e.key === 'Escape') {
      setOpen(false)
      dismissExchange()
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
            dismissExchange()
            setQuery(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder={asking ? 'The agent is working…' : 'Search, or ask the agent…'}
          aria-label="Search projects and tasks, or ask the agent"
          role="combobox"
          aria-expanded={showDropdown}
          aria-controls="command-search-results"
          disabled={asking}
        />
        <kbd>Cmd K</kbd>
      </div>

      {exchangeVisible && (
        <InlineAgentExchange
          state={askState}
          onContinue={(conversationId) => {
            dismissExchange()
            reset()
            navigate(`/agent/${conversationId}`)
          }}
        />
      )}

      {showDropdown && (
        <Card
          as="div"
          className="command-search-dropdown"
          id="command-search-results"
          role="listbox"
        >
          <AsyncState
            loading={loading}
            error={error}
            isEmpty={flat.length === 0}
            loadingLabel="Searching…"
            emptyLabel={`No matches for “${trimmed}”.`}
          >
            {groups.map((group) => (
              <div key={group.label ?? '_'} className="command-search-group">
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
                          className={
                            index === activeIndex
                              ? 'command-search-result active'
                              : 'command-search-result'
                          }
                          // Pointer enter keeps mouse + keyboard highlight in sync.
                          onMouseEnter={() => setCurrentActiveIndex(index)}
                          onClick={row.onSelect}
                        >
                          <Badge tone={row.badge.tone}>{row.badge.label}</Badge>
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
          {/* Discoverability footer for the plain-Enter ask. Deliberately
              outside the arrow-key row flow — Enter only asks when no result
              row is highlighted. */}
          <div className="command-search-ask-footer">
            <button
              type="button"
              className="command-search-ask"
              onClick={() => void submitAsk()}
            >
              <SpiderMark size={16} />
              <span className="command-search-result-text">
                <span className="command-search-result-title">
                  Ask the agent: “{trimmed}”
                </span>
              </span>
              <kbd>Enter</kbd>
            </button>
          </div>
        </Card>
      )}
    </div>
  )
}
