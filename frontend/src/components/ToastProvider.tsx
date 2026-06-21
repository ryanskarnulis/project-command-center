import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

export type ToastKind = 'success' | 'error'

interface Toast {
  id: number
  kind: ToastKind
  message: string
}

interface ToastApi {
  notify: (kind: ToastKind, message: string) => void
  /** Convenience wrapper: notify success, or an error toast if the promise rejects. */
  withToast: <T>(
    promise: Promise<T>,
    messages: { success: string; error?: string },
  ) => Promise<T>
}

// No-op default so components/hooks render fine without a provider (mirrors
// TrashCountContext) — toasts simply don't show, which never breaks a flow.
const ToastContext = createContext<ToastApi>({
  notify: () => {},
  withToast: async (promise) => promise,
})

const DISMISS_MS = 4000

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(0)
  const timers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map())

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
    const timer = timers.current.get(id)
    if (timer) {
      clearTimeout(timer)
      timers.current.delete(id)
    }
  }, [])

  const notify = useCallback(
    (kind: ToastKind, message: string) => {
      const id = nextId.current++
      setToasts((prev) => [...prev, { id, kind, message }])
      timers.current.set(
        id,
        setTimeout(() => dismiss(id), DISMISS_MS),
      )
    },
    [dismiss],
  )

  const withToast = useCallback(
    async <T,>(
      promise: Promise<T>,
      messages: { success: string; error?: string },
    ): Promise<T> => {
      try {
        const result = await promise
        notify('success', messages.success)
        return result
      } catch (e: unknown) {
        const fallback =
          messages.error ??
          (e instanceof Error ? e.message : 'Something went wrong')
        notify('error', fallback)
        throw e
      }
    },
    [notify],
  )

  // Clear any pending timers on unmount so we don't set state on a gone tree.
  useEffect(() => {
    const map = timers.current
    return () => {
      map.forEach(clearTimeout)
      map.clear()
    }
  }, [])

  // Memoized so consumers depending on `withToast`/`notify` in useCallback deps
  // don't churn when the provider re-renders to show/hide a toast.
  const api = useMemo<ToastApi>(() => ({ notify, withToast }), [notify, withToast])

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-stack" role="region" aria-label="Notifications">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`toast toast--${toast.kind}`}
            role={toast.kind === 'error' ? 'alert' : 'status'}
          >
            <span>{toast.message}</span>
            <button
              type="button"
              className="toast-dismiss"
              aria-label="Dismiss notification"
              onClick={() => dismiss(toast.id)}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  return useContext(ToastContext)
}
