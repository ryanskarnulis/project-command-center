import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Sparkles } from 'lucide-react'
import { Badge, type BadgeTone } from '../../components/Badge'
import { Card } from '../../components/Card'
import { AsyncState } from '../../components/AsyncState'
import type { SearchKind, SearchResultItem } from '../../types/search'
import { useSearch } from './useSearch'

// Per-kind display metadata. The bar is intentionally generic (it will later host
// `/done`, `/new`, and AI chat) so routing/labels live in data, not branching JSX.
const KIND_META: Record<
  SearchKind,
  { label: string; tone: BadgeTone; path: (item: SearchResultItem) => string }
> = {
  project: { label: 'Project', tone: 'blue', path: (i) => `/projects/${i.id}` },
  task: { label: 'Task', tone: 'purple', path: (i) => `/tasks/${i.id}` },
  inbox: { label: 'Inbox', tone: 'orange', path: (i) => `/inbox/${i.id}` },
}

export function CommandSearch() {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const { results, loading, error, total } = useSearch(query)
  const navigate = useNavigate()
  const containerRef = useRef<HTMLDivElement>(null)

  // Ordered, non-empty groups; `flat` mirrors the render order for keyboard nav.
  const groups = useMemo(
    () =>
      (
        [
          { label: 'Projects', items: results.projects },
          { label: 'Tasks', items: results.tasks },
          { label: 'Inbox', items: results.inbox_items },
        ] as const
      ).filter((g) => g.items.length > 0),
    [results],
  )
  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups])

  // A new result set invalidates the previous highlight.
  useEffect(() => setActiveIndex(-1), [results])

  // Close the dropdown when focus/click leaves the bar.
  useEffect(() => {
    function onPointerDown(e: MouseEvent) {
      if (!containerRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [])

  const trimmed = query.trim()
  const showDropdown = open && trimmed !== ''

  function go(item: SearchResultItem) {
    navigate(KIND_META[item.kind].path(item))
    setOpen(false)
    setQuery('')
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setOpen(true)
      setActiveIndex((i) => Math.min(i + 1, flat.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      if (activeIndex >= 0 && flat[activeIndex]) {
        e.preventDefault()
        go(flat[activeIndex])
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
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder="Search projects, tasks, inbox…"
          aria-label="Search projects, tasks, and inbox"
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
          <AsyncState
            loading={loading}
            error={error}
            isEmpty={total === 0}
            loadingLabel="Searching…"
            emptyLabel={`No matches for “${trimmed}”.`}
          >
            {groups.map((group) => (
              <div key={group.label} className="command-search-group">
                <p className="command-search-group-label">{group.label}</p>
                <ul>
                  {group.items.map((item) => {
                    globalIndex += 1
                    const index = globalIndex
                    return (
                      <li key={`${item.kind}-${item.id}`}>
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
                          onMouseEnter={() => setActiveIndex(index)}
                          onClick={() => go(item)}
                        >
                          <Badge tone={KIND_META[item.kind].tone}>
                            {KIND_META[item.kind].label}
                          </Badge>
                          <span className="command-search-result-text">
                            <span className="command-search-result-title">
                              {item.title}
                            </span>
                            {item.subtitle && (
                              <span className="command-search-result-subtitle">
                                {item.subtitle}
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
        </Card>
      )}
    </div>
  )
}
