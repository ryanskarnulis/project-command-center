import { useSyncExternalStore } from 'react'

/* The phone breakpoint shared by the per-project routes: `/projects/:id`
   (M06f) and `/projects/:id/tasks` (M02f/M03f). Below it each page swaps its
   desktop tree for the mobile surface; the matching CSS is mobile-tasks.css. */
const query = '(max-width: 720px)'

function subscribe(notify: () => void): () => void {
  const media = window.matchMedia?.(query)
  media?.addEventListener('change', notify)
  return () => media?.removeEventListener('change', notify)
}

export function useMobileViewport(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia?.(query).matches ?? false,
    () => false,
  )
}
