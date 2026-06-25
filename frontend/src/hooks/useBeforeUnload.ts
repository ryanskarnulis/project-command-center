import { useEffect } from 'react'

/**
 * Prompts the native browser "Leave site?" dialog on refresh / tab-close while
 * `dirty` is true. The listener is attached only while dirty and removed as soon
 * as the edit is saved (dirty flips false) or the component unmounts.
 *
 * This guards refresh/close only — in-app navigation is expected to be covered
 * by save-on-blur or React Router's useBlocker at the call site.
 */
export function useBeforeUnload(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])
}
