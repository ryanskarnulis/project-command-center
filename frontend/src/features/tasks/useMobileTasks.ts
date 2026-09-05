import { useSyncExternalStore } from 'react'

const query = '(max-width: 720px)'
function subscribe(notify: () => void): () => void {
  const media = window.matchMedia?.(query)
  media?.addEventListener('change', notify)
  return () => media?.removeEventListener('change', notify)
}

export function useMobileTasks(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia?.(query).matches ?? false,
    () => false,
  )
}
